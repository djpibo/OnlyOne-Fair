from api.supabase.model.common import LoginDTO
from api.supabase.model.point import ConsumeInfoDTO
from api.supabase.model.presentation import ScreenDTO
from api.supabase.model.quiz import ScoreInfoDTO
from common.constants import *
from common.util import ScoreUtil, CommonUtil
from config.connect import connect_redis
from service.common_service import CommonMgr
from service.consume_point import PointMgr
from service.room_stay_service import EnterMgr, ExitMgr, ScoreMgr

class Commander:
    def __init__(self, enter_mgr: EnterMgr, exit_mgr: ExitMgr, score_mgr: ScoreMgr, common_mgr: CommonMgr, point_mgr: PointMgr):
        self.exit_mgr = exit_mgr
        self.enter_mgr = enter_mgr
        self.score_mgr = score_mgr
        self.common_mgr = common_mgr
        self.point_mgr = point_mgr
        self.redis = connect_redis()

    def start_card_polling(self, nfc_uid):

        if nfc_uid is not None:
            # 각 사 교육지도자인 경우, skip
            if self.common_mgr.validate_teacher(nfc_uid):
                print(f"[log] 운영진 혹은 TF 인원입니다.")
                return ScreenDTO(peer_company="ONLYONE FAIR", peer_name="운영진", enter_dvcd_kor="", used=0,
                                acc_score=0, current_score=0, comment="촬영권 무한, 포인트 적용 대상 X")

            # 최초 태그 및 특정 순번 태그 인원 식별
            self.common_mgr.count_up(nfc_uid)
            argv1 = self.redis.get('company').decode('utf-8')
            argv2 = self.redis.get('enter').decode('utf-8')

            login_dto = self.common_mgr.login_setter(argv1, argv2, nfc_uid)
            if login_dto.enter_dvcd == ENTER_DVCD_ENTRANCE:
                scr_dto = self.validate_enter(login_dto)  # 입장 검증
                if scr_dto is not None:
                    return scr_dto
                return self.process_enter(login_dto)  # 입장 처리

            elif login_dto.enter_dvcd == ENTER_DVCD_EXIT:
                recent_enter_info = self.enter_mgr.get_latest_enter(login_dto)
                scr_dto = self.validate_exit(recent_enter_info, login_dto)
                if scr_dto is not None:
                    return scr_dto
                return self.process_exit(login_dto, recent_enter_info)  # 퇴장 처리

            elif login_dto.enter_dvcd == ENTER_DVCD_PHOTO:
                return self.point_consumer(login_dto)

            else:
                return self.process_welcome(login_dto)

        else:
            print(f"[ERROR] NFC UID 수신 오류")
            return None

    def validate_enter(self, login_dto:LoginDTO):

        # TODO 최초에 한 번 입장한 경우, 이전 입장

        user_not_checked_exit = self.enter_mgr.get_unchecked_exit(login_dto)
        if user_not_checked_exit is not None:  # 퇴장을 찍고 오지 않은 경우 이전 부스 입장 내역이 남아 있다

            score = CommonUtil.get_min_time_by_company_dvcd(
                user_not_checked_exit.company_dvcd) if ScoreUtil.check_min_stay_time(user_not_checked_exit) else 0

            # 1-퇴장 처리(입장(또는 재입장) => True, 퇴장 insert)
            self.exit_mgr.set_enter_exit(user_not_checked_exit)
            self.exit_mgr.set_exit_true(user_not_checked_exit)

            # 2-최소 점수 받기 (그리고 입장)
            set_to_score_info = ScoreInfoDTO(
                id=login_dto.peer_id,
                quiz_dvcd=QUIZ_DVCD_NFC_EXIST_TIME,
                company_dvcd=user_not_checked_exit.company_dvcd,
                score=score
            )
            self.score_mgr.set_score(set_to_score_info)

            # GUI case 1-다른 클래스에서 퇴실 안찍고 입장한 경우
            acc_score = self.score_mgr.get_current_point(login_dto)
            used_score = self.point_mgr.get_used_point(login_dto)
            current_score = score
            comment = (f"{self.common_mgr.get_common_desc(user_not_checked_exit.company_dvcd)}은/는"
                       f" 최소 점수({current_score})로 퇴장 처리됐습니다.\n"
                       f" 입장 처리를 위해, 한 번 더 ONLYONE BAND를 태그해주세요.")
            scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, used_score=used_score, acc_score=acc_score,
                                enter_dvcd_kor="입장대기", current_score=current_score, comment=comment)

            print(f"[log] 최소 점수로 입장 처리. 클래스명: "
                  f"{self.common_mgr.get_common_desc(user_not_checked_exit.company_dvcd)}")

            return scr_dto

        return None

    def process_enter(self, login_dto:LoginDTO):
        reenter_enter_info = self.enter_mgr.check_exit_before(login_dto)
        if reenter_enter_info is not None:  # 퇴장 여부가 있다는 것은 재입장이라는 뜻
            print("[log] 재입장 처리 진행")
            self.enter_mgr.set_to_reenter(reenter_enter_info)

            # 최대 포인트 충족 검증
            if self.enter_mgr.validate_if_full(login_dto):
                comment = (f"{self.common_mgr.get_common_desc(login_dto.argv_company_dvcd)} 클래스에서\n"
                           f"획득 가능한 포인트는 모두 채우셨습니다\n다른 클래스를 방문해보시는 것은 어떨까요?")
            else:
                comment = "재입장인 경우, 입장 포인트는 없습니다."

            acc_score = self.score_mgr.get_current_point(login_dto)
            used_score = self.point_mgr.get_used_point(login_dto)
            scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="재입장", used_score=used_score,
                                acc_score=acc_score, current_score=0, comment=comment)
            #ScreenMgr.draw_whole(self.screen_mgr, scr_dto)
            return scr_dto

        else:  # 최초 입장
            print("[log] 최초 입장 처리 진행")
            # 입장하는 클래스에 따라 경과 시간 분기
            # 입장 포인트 부여
            self.score_mgr.set_entrance_point(login_dto)
            self.enter_mgr.set_to_enter(login_dto)
            acc_score = self.score_mgr.get_current_point(login_dto)
            used_score = self.point_mgr.get_used_point(login_dto)
            current_score = 50
            comment = "입장 포인트 50점 획득"
            scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="입장", used_score=used_score,
                                acc_score=acc_score, current_score=current_score, comment=comment,
                                require_time=8 if login_dto.argv_company_dvcd in BIG_ROOM_COMPANY else 3)
            #ScreenMgr.draw_whole(self.screen_mgr, scr_dto)

            return scr_dto

    def validate_exit(self, recent_enter_info, login_dto):

        # 검증 : 입장 안 찍고 퇴장 먼저 하는 경우
        if recent_enter_info is None:
            comment = ""
            if CommonUtil.is_less_than_one_minute_interval(self.enter_mgr.get_latest_exit(login_dto)):
                print(f"[log] 연속 거래 방지")
                comment = (f"{login_dto.peer_name}님은 이미 퇴장 처리 되었습니다"
                           f"\n다른 클래스를 방문해보는 것은 어떨까요?")

            else:
                print("[error] 입장 먼저 하세요.")
                comment = f"입실 리더기에 ONLYONE BAND를 태그해주세요."

            acc_score = self.score_mgr.get_current_point(login_dto)
            used_score = self.point_mgr.get_used_point(login_dto)
            scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="잘못된 리더기에 태그", used=used_score,
                                acc_score=acc_score, current_score=0, comment=comment)
            return scr_dto

        else:
            # 검증 : 입장 클래스와 퇴장 클래스가 다른 경우
            if recent_enter_info.company_dvcd != login_dto.argv_company_dvcd:
                print("[error] (퇴장 검증) 입장 클래스와 퇴장 클래스가 다른 경우 ")
                acc_score = self.score_mgr.get_current_point(login_dto)
                used_score = self.point_mgr.get_used_point(login_dto)
                comment = (f"{login_dto.peer_name}님"
                           f"\n{self.common_mgr.get_common_desc(recent_enter_info.company_dvcd)}에서 퇴실 처리를 하지 않았습니다."
                           f"\n 입실 리더기에 ONLYONE BAND를 태그해주세요."
                           f"\n (❗️체류 시간에 따른 획득 포인트 불이익 발생 가능)")
                scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="잘못된 리더기에 태그", used=used_score,
                                    acc_score=acc_score, current_score=0, comment=comment)
                return scr_dto

            else:
                # 체류 시간 포인트 계산 (체류 시간 : 현재 시각 - 최근 입장 시각)
                current_exp_point = ScoreUtil.calculate_entrance_score(recent_enter_info.created_at)

                # 최초 입장에 대한 퇴장인 경우, 최소 잔류 시간 검증
                if recent_enter_info.enter_dvcd == ENTER_DVCD_ENTRANCE:

                    # 클래스별 최소 체류 시간 확인
                    min_point = CommonUtil.get_min_time_by_company_dvcd(login_dto.argv_company_dvcd)

                    # 클래스별 최소 체류 시간을 충족하지 못한 경우, 1회에 한해 퇴장 차단
                    if current_exp_point < min_point:
                        value = self.redis.get(login_dto.peer_id)
                        if value is None:
                            self.redis.set(login_dto.peer_id, 0)

                            acc_score = self.score_mgr.get_current_point(login_dto)
                            used_score = self.point_mgr.get_used_point(login_dto)
                            comment = (
                                f"경험 시간이 {format(ScoreUtil.calculate_time_by_score(min_point, current_exp_point))} 부족합니다."
                                f"\n그래도 퇴실하시려면 한 번 더 태그해주세요 (❗️0점 처리)")
                            scr_dto = ScreenDTO(peer_company=login_dto.peer_company,
                                                peer_name=login_dto.peer_name,
                                                enter_dvcd_kor="최소 경험시간 미충족",
                                                used=used_score,
                                                acc_score=acc_score,
                                                current_score=0,
                                                comment=comment)
                            return scr_dto

    def process_exit(self, login_dto:LoginDTO, recent_enter_info):

        # 체류 시간 계산
        current_exp_point = ScoreUtil.calculate_entrance_score(recent_enter_info.created_at)
        # 각 사별 상한 포인트
        max_point = CommonUtil.get_max_time_by_company_dvcd(login_dto.argv_company_dvcd)

        # 상한 시간 검증을 위한 이전 누적 시간 집계
        score_info_dto = ScoreInfoDTO(
            id=login_dto.peer_id, quiz_dvcd=QUIZ_DVCD_NFC_EXIST_TIME, company_dvcd=login_dto.argv_company_dvcd, score=0)
        bf_exp_point = self.score_mgr.get_exp_score(score_info_dto)

        # 동적 분기 처리를 위한 변수 초기화
        update_point = 0
        screen_point = 0
        _comment = ""

        # 이미 만점으로 입장한 경우
        if bf_exp_point >= max_point:
            screen_point = 0
            update_point = max_point
            _comment = (f"{self.common_mgr.get_common_desc(login_dto.argv_company_dvcd)} 클래스에서\n"
                        f"획득 가능한 포인트는 모두 채우셨습니다\n다른 클래스를 방문해보시는 것은 어떨까요?")

        # 만점을 넘은 경우, 상한 포인트로 제한
        elif current_exp_point > (max_point - bf_exp_point):
            screen_point = max_point - bf_exp_point
            update_point = max_point
            _comment = (f"입실시간 기록완료 🪄 받은 포인트 : {int(current_exp_point)}\n"
                        f"{self.common_mgr.get_common_desc(login_dto.argv_company_dvcd)} 클래스에서\n"
                        f"획득 가능한 포인트는 모두 채우셨습니다")

        # 정상 시간 범위
        else:
            screen_point = current_exp_point
            update_point = current_exp_point

            # 강제 퇴실 동의 받고 온 경우, 0점 처리
            if self.redis.get(login_dto.peer_id) is not None:
                print(f"[test] 강제 퇴장 처리 {self.redis.get(login_dto.peer_id)}")
                screen_point = 0
                update_point = 0
                self.redis.delete(login_dto.peer_id)

            _comment = f"입실시간 기록완료 🪄 받은 포인트 : {int(screen_point)}"

        print("[log] 퇴장 처리 진행")
        self.exit_mgr.set_enter_exit(recent_enter_info)  # latest 입장 > 퇴장 여부 True
        self.exit_mgr.set_exit_true(recent_enter_info)  # 실제 퇴장 insert
        stay_score_info = ScoreInfoDTO(
            id=login_dto.peer_id,
            quiz_dvcd=QUIZ_DVCD_NFC_EXIST_TIME,
            company_dvcd=login_dto.argv_company_dvcd,
            score=update_point
        )
        self.score_mgr.set_score(stay_score_info)

        acc_score = self.score_mgr.get_current_point(login_dto)
        used_score = self.point_mgr.get_used_point(login_dto)
        scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="퇴장", used_score=used_score,
                            acc_score=acc_score, current_score=screen_point, comment=_comment)
        return scr_dto

    def point_consumer(self, login_dto):
        consumer = login_dto.peer_id

        # 1 연속 거래 방지
        if CommonUtil.is_less_than_one_minute_interval(self.point_mgr.get_latest_consume(login_dto)):
            print(f"[log] 연속 거래 방지")

        # 2 누적 포인트에 기반해서 계산
        current_point = self.score_mgr.get_current_point(LoginDTO(peer_id=consumer, argv_company_dvcd=99))
        used_score = self.point_mgr.get_used_point(login_dto)

        comment = ""
        # 2-1 조건 검증
        if (current_point - used_score)> CONSUME_PHOTO_POINT:

            # 3 포인트 차감 처리 (insert++, used_point)
            consume_dto = ConsumeInfoDTO(id=consumer, consume_dvcd=CONSUME_PHOTO_DVCD, used_score=CONSUME_PHOTO_POINT)
            self.point_mgr.consume_point(consume_dto)
            comment = " 📸 촬영권 1매 사용:)"
        else:
            comment = "❗️포인트가 부족합니다:("

        acc_score = self.score_mgr.get_current_point(login_dto)
        scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, enter_dvcd_kor="촬영권 사용", used_score=used_score,
                            acc_score=acc_score, current_score=0, comment=comment)
        return scr_dto

    def process_welcome(self, login_dto:LoginDTO):
        comment = (f"ONLYONE FAIR 공유회에 오신 것을 환영합니다! ⭐\n 지난 5주 간 정말 고생 많았어요 {login_dto.peer_name}님 ❤️\n오늘은 여정을 마무리하는 뜻 깊은 하루가 되길 바랄게요 🍀")
        scr_dto = ScreenDTO(peer_company=login_dto.peer_company, peer_name=login_dto.peer_name, used_score=0,
                            acc_score=0,
                            enter_dvcd_kor="🫡", current_score=0, comment=comment)

        print("[log] 출석 처리")

        return scr_dto


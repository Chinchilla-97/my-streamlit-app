import hmac
import io
import os
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta

import streamlit as st

st.set_page_config(page_title="용산구가족센터 서포터즈 활동일지", page_icon="📂")

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
UPLOAD_DIR = "activity_logs"
ALLOWED_EXT = {".hwp", ".hwpx", ".jpg", ".jpeg", ".png", ".pdf"}
ACTIVITY_TYPES = ["대면", "비대면"]
MAX_FILE_MB = 20

os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_secret(key: str, fallback: str) -> str:
    """배포 시에는 .streamlit/secrets.toml 에 값을 넣어두고 사용."""
    try:
        return st.secrets[key]
    except Exception:
        return fallback


PASSWORD = get_secret("APP_PASSWORD", "change-me")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "change-me-admin")


def check_password(entered: str, real: str) -> bool:
    # 한글 등 비ASCII 문자를 쓰려면 바이트로 변환해서 비교해야 함
    return hmac.compare_digest(
        str(entered).encode("utf-8"), str(real).encode("utf-8")
    )


def safe_name(text: str) -> str:
    """경로 조작·특수문자 제거. 한글은 유지."""
    text = unicodedata.normalize("NFC", str(text)).strip()
    text = os.path.basename(text.replace("\\", "/"))
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = text.strip("._")
    return text[:80] or "unnamed"


def safe_field(text: str) -> str:
    """이름·활동종류용. 밑줄(_)을 구분자로 쓰므로 밑줄은 남기지 않는다."""
    text = unicodedata.normalize("NFC", str(text)).strip()
    text = os.path.basename(text.replace("\\", "/"))
    text = re.sub(r"[^0-9A-Za-z가-힣.-]+", "-", text)
    text = text.strip("-.")
    return text[:40] or "unnamed"


def parse_stored(filename: str):
    """저장 파일명에서 활동일자·활동종류·이름을 뽑아낸다. 실패하면 None."""
    stem, ext = os.path.splitext(filename)
    parts = stem.split("_")
    if len(parts) < 3 or parts[1] not in ACTIVITY_TYPES:
        return None
    try:
        parsed_date = datetime.strptime(parts[0], "%Y%m%d").date()
    except ValueError:
        return None
    return {"date": parsed_date, "type": parts[1], "name": parts[2], "ext": ext}


def week_start(day: date) -> date:
    """그 주의 일요일."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def week_label(start: date) -> str:
    """예: 8월 1주 (8/2~8/8)"""
    end = start + timedelta(days=6)
    nth = (start.day - 1) // 7 + 1
    year = f"{start.year}년 " if start.year != date.today().year else ""
    return f"{year}{start.month}월 {nth}주 ({start.month}/{start.day}~{end.month}/{end.day})"


def download_name(info: dict, seq: int = 1) -> str:
    """관리자 다운로드용 이름: 이름_활동일자_활동종류"""
    base = f"{info['name']}_{info['date']:%Y%m%d}_{info['type']}"
    if seq > 1:
        base += f"-{seq}"
    return base + info["ext"]


def unique_path(directory: str, filename: str) -> str:
    """제출 시각(HHMM)을 붙여 저장. 그래도 겹치면 (1), (2) … 를 덧붙임."""
    stem, ext = os.path.splitext(filename)
    stem = f"{stem}_{datetime.now():%H%M}"
    candidate = os.path.join(directory, f"{stem}{ext}")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}({counter}){ext}")
        counter += 1
    return candidate


# ----------------------------------------------------------------------
# 로그인
# ----------------------------------------------------------------------
st.title("📂 [용산구가족센터] 서포터즈 활동일지 제출 시스템")

st.session_state.setdefault("authenticated", False)

if not st.session_state.authenticated:
    pw = st.text_input("접속 비밀번호를 입력하세요", type="password")
    if st.button("로그인"):
        if check_password(pw, PASSWORD):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.success("로그인 성공")
if st.button("로그아웃"):
    st.session_state.authenticated = False
    st.rerun()

# ----------------------------------------------------------------------
# 제출 폼
# ----------------------------------------------------------------------
with st.form("log_upload_form", clear_on_submit=True):
    activist_name = st.text_input("서포터즈 이름")
    log_date = st.date_input(
        "활동 일자 (지난 날짜도 선택 가능)",
        value=date.today(),
        min_value=date.today() - timedelta(days=730),
        max_value=date.today(),
        format="YYYY-MM-DD",
    )
    activity_type = st.radio(
        "활동 종류",
        options=ACTIVITY_TYPES,
        horizontal=True,
    )
    uploaded_files = st.file_uploader(
        f"파일 첨부 (HWP, HWPX, JPG, PNG, PDF · 파일당 최대 {MAX_FILE_MB}MB)",
        type=[e.lstrip(".") for e in sorted(ALLOWED_EXT)],
        accept_multiple_files=True,
    )
    submitted = st.form_submit_button("제출하기")

if submitted:
    if not activist_name.strip() or not uploaded_files:
        st.warning("이름과 파일을 모두 입력해주세요.")
    else:
        date_str = log_date.strftime("%Y%m%d")
        name_part = safe_field(activist_name)
        type_part = safe_field(activity_type)
        saved, skipped = [], []

        for uploaded_file in uploaded_files:
            original = safe_name(uploaded_file.name)
            ext = os.path.splitext(original)[1].lower()

            if ext not in ALLOWED_EXT:
                skipped.append(f"{uploaded_file.name} (허용되지 않는 형식)")
                continue
            if uploaded_file.size > MAX_FILE_MB * 1024 * 1024:
                skipped.append(f"{uploaded_file.name} (용량 초과)")
                continue

            target = unique_path(
                UPLOAD_DIR,
                f"{date_str}_{type_part}_{name_part}_{original}",
            )
            try:
                with open(target, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                saved.append(os.path.basename(target))
            except OSError as err:
                skipped.append(f"{uploaded_file.name} (저장 실패: {err})")

        if saved:
            st.success(f"{activist_name}님, {len(saved)}개 파일이 제출되었습니다.")
            for name in saved:
                st.caption(f"저장됨: {name}")
        if skipped:
            st.error("제출되지 않은 파일:\n" + "\n".join(f"- {s}" for s in skipped))

# ----------------------------------------------------------------------
# 관리자
# ----------------------------------------------------------------------
with st.expander("관리자 확인용"):
    admin_pw = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
    if admin_pw and check_password(admin_pw, ADMIN_PASSWORD):
        st.subheader("📋 제출 현황")
        stored_files = sorted(os.listdir(UPLOAD_DIR), reverse=True)

        if not stored_files:
            st.info("아직 제출된 파일이 없습니다.")
        else:
            records = []
            for stored in stored_files:
                info = parse_stored(stored)
                if info is None:  # 예전 방식으로 저장된 파일
                    info = {
                        "date": None,
                        "type": "미분류",
                        "name": "미분류",
                        "ext": os.path.splitext(stored)[1],
                    }
                records.append((stored, info))

            # ---- 활동 종류로 걸러내기 ----
            types_present = [t for t in ACTIVITY_TYPES + ["미분류"]
                             if any(i["type"] == t for _, i in records)]
            counts = {t: sum(i["type"] == t for _, i in records) for t in types_present}
            st.caption(
                "  ·  ".join(f"{t} {c}건" for t, c in counts.items())
                + f"  ·  전체 {len(records)}건"
            )
            type_choice = st.radio(
                "활동 종류",
                options=["전체"] + types_present,
                horizontal=True,
                key="admin_type",
            )
            if type_choice != "전체":
                records = [r for r in records if r[1]["type"] == type_choice]

            # ---- 주차로 묶기 ----
            weeks = {}
            for stored, info in records:
                key = week_start(info["date"]) if info["date"] else None
                weeks.setdefault(key, []).append((stored, info))

            week_keys = sorted([k for k in weeks if k is not None], reverse=True)
            labels = ["전체 보기"] + [
                f"{week_label(k)} · {len(weeks[k])}건" for k in week_keys
            ]
            if None in weeks:
                labels.append(f"날짜 미확인 · {len(weeks[None])}건")

            picked = st.selectbox(
                "주차 선택",
                options=labels,
                index=1 if week_keys else 0,
                key="admin_week",
            )

            if picked == "전체 보기":
                shown = records
            elif picked.startswith("날짜 미확인"):
                shown = weeks[None]
            else:
                shown = weeks[week_keys[labels.index(picked) - 1]]

            # ---- 다운로드용 이름 만들기 (이름_활동일자_활동종류) ----
            used, rows = {}, []
            for stored, info in sorted(
                shown, key=lambda r: (r[1]["date"] or date.min, r[1]["name"]), reverse=True
            ):
                if info["date"] is None:
                    new_name = stored
                else:
                    key = download_name(info)
                    used[key] = used.get(key, 0) + 1
                    new_name = download_name(info, used[key])
                rows.append((stored, new_name))

            # ---- 선택한 주차 전체를 ZIP으로 ----
            if rows:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for stored, new_name in rows:
                        zf.write(os.path.join(UPLOAD_DIR, stored), arcname=new_name)
                zip_label = picked.split(" · ")[0].replace(" ", "")
                st.download_button(
                    f"📦 {len(rows)}개 파일 한번에 받기 (ZIP)",
                    buffer.getvalue(),
                    file_name=f"활동일지_{zip_label}.zip",
                    mime="application/zip",
                )
                st.divider()

            # ---- 개별 목록 ----
            for stored, new_name in rows:
                path = os.path.join(UPLOAD_DIR, stored)
                size_kb = os.path.getsize(path) / 1024
                col1, col2 = st.columns([4, 1])
                col1.write(f"**{new_name}**")
                col1.caption(f"원본: {stored}  ·  {size_kb:,.0f}KB")
                with open(path, "rb") as f:
                    col2.download_button(
                        "받기", f.read(), file_name=new_name, key=f"dl_{stored}"
                    )
    elif admin_pw:
        st.error("관리자 비밀번호가 다릅니다.")

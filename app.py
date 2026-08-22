import base64
import hmac
import io
import mimetypes
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
GAS_TIMEOUT = 120  # 초

os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_secret(key: str, fallback: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return fallback


PASSWORD = get_secret("APP_PASSWORD", "change-me")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "change-me-admin")


def gas_config():
    """secrets 에 [gas] url·secret 이 있으면 구글 드라이브 모드로 동작."""
    try:
        conf = st.secrets["gas"]
    except Exception:
        return None
    if str(conf.get("url", "")).strip() and str(conf.get("secret", "")).strip():
        return {"url": str(conf["url"]).strip(), "secret": str(conf["secret"]).strip()}
    return None


GAS = gas_config()


def gas_call(action: str, **payload):
    """Apps Script 중계기에 요청을 보내고 결과를 돌려준다."""
    import requests

    body = {"secret": GAS["secret"], "action": action}
    body.update(payload)
    res = requests.post(GAS["url"], json=body, timeout=GAS_TIMEOUT)
    res.raise_for_status()
    try:
        data = res.json()
    except ValueError:
        raise RuntimeError(
            "응답을 읽을 수 없습니다. 배포 주소가 /exec 로 끝나는지, "
            "액세스 권한이 '모든 사용자'인지 확인해주세요."
        )
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


# ----------------------------------------------------------------------
# 공통 도구
# ----------------------------------------------------------------------
def check_password(entered: str, real: str) -> bool:
    # 한글 등 비ASCII 문자를 쓰려면 바이트로 변환해서 비교해야 함
    return hmac.compare_digest(
        str(entered).encode("utf-8"), str(real).encode("utf-8")
    )


def safe_field(text: str) -> str:
    """이름·활동종류용. 밑줄(_)을 구분자로 쓰므로 밑줄은 남기지 않는다."""
    text = unicodedata.normalize("NFC", str(text)).strip()
    text = os.path.basename(text.replace("\\", "/"))
    text = re.sub(r"[^0-9A-Za-z가-힣.-]+", "-", text)
    text = text.strip("-.")
    return text[:40] or "unnamed"


def parse_name(filename: str):
    """저장 파일명(이름_활동일자_활동종류)에서 정보를 뽑아낸다. 실패하면 None."""
    stem, ext = os.path.splitext(filename)
    parts = stem.split("_")
    if len(parts) < 3:
        return None
    person, datestr, kind = parts[0], parts[1], re.sub(r"-\d+$", "", parts[2])
    if kind not in ACTIVITY_TYPES:
        return None
    try:
        parsed = datetime.strptime(datestr, "%Y%m%d").date()
    except ValueError:
        return None
    return {"name": person, "date": parsed, "type": kind, "ext": ext}


def unique_name(existing, base: str, ext: str) -> str:
    """같은 이름이 있으면 -2, -3 을 붙여 덮어쓰기를 막는다."""
    candidate = f"{base}{ext}"
    seq = 2
    while candidate in existing:
        candidate = f"{base}-{seq}{ext}"
        seq += 1
    return candidate


def parse_created(text):
    """Apps Script 가 보내온 제출일시(한국 시각) 문자열을 날짜로 바꾼다."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def week_start(day: date) -> date:
    """그 주의 일요일."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def week_label(start: date) -> str:
    """예: 8월 1주 (8/2~8/8)"""
    end = start + timedelta(days=6)
    nth = (start.day - 1) // 7 + 1
    year = f"{start.year}년 " if start.year != date.today().year else ""
    return f"{year}{start.month}월 {nth}주 ({start.month}/{start.day}~{end.month}/{end.day})"


# ----------------------------------------------------------------------
# 저장소 (구글 드라이브 또는 임시 서버 폴더)
# ----------------------------------------------------------------------
def list_items():
    """[{id, name, size, link}] 목록을 돌려준다."""
    if GAS:
        data = gas_call("list")
        st.session_state["drive_folder_id"] = data.get("folderId")
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "size": int(f.get("size") or 0),
                "link": f.get("link"),
                "created": parse_created(f.get("created")),
            }
            for f in data.get("files", [])
        ]

    items = []
    for name in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.isfile(path):
            items.append(
                {
                    "id": name,
                    "name": name,
                    "size": os.path.getsize(path),
                    "link": None,
                    "created": datetime.fromtimestamp(os.path.getmtime(path)),
                }
            )
    return items


def save_item(name: str, data: bytes):
    if GAS:
        gas_call(
            "upload",
            name=name,
            mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
            data=base64.b64encode(data).decode("ascii"),
        )
    else:
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(data)


def read_item(item) -> bytes:
    if GAS:
        return base64.b64decode(gas_call("get", id=item["id"])["data"])
    with open(os.path.join(UPLOAD_DIR, item["id"]), "rb") as f:
        return f.read()


def delete_item(item) -> bool:
    """드라이브는 휴지통으로 이동, 임시 폴더는 삭제."""
    if GAS:
        gas_call("delete", id=item["id"])
        return True
    base = os.path.abspath(UPLOAD_DIR)
    target = os.path.abspath(os.path.join(base, os.path.basename(item["id"])))
    if not target.startswith(base + os.sep) or not os.path.isfile(target):
        return False
    os.remove(target)
    return True


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

if not GAS:
    st.warning(
        "임시 저장 모드입니다. 앱이 다시 시작되면 파일이 사라지니 "
        "구글 드라이브 연결 설정을 마쳐주세요.",
        icon="⚠️",
    )

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
    activity_type = st.radio("활동 종류", options=ACTIVITY_TYPES, horizontal=True)
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
        base_name = f"{safe_field(activist_name)}_{log_date:%Y%m%d}_{safe_field(activity_type)}"
        proceed = True

        with st.spinner("제출 중입니다… 잠시만 기다려주세요."):
            try:
                taken = {i["name"] for i in list_items()}
            except Exception as err:
                st.error(f"저장소에 연결하지 못했습니다: {err}")
                proceed = False
                taken = set()

            saved, skipped = [], []
            if proceed:
                for uploaded_file in uploaded_files:
                    ext = os.path.splitext(uploaded_file.name)[1].lower()
                    if ext not in ALLOWED_EXT:
                        skipped.append(f"{uploaded_file.name} (허용되지 않는 형식)")
                        continue
                    if uploaded_file.size > MAX_FILE_MB * 1024 * 1024:
                        skipped.append(f"{uploaded_file.name} (용량 초과)")
                        continue

                    final_name = unique_name(taken, base_name, ext)
                    try:
                        save_item(final_name, uploaded_file.getvalue())
                        taken.add(final_name)
                        saved.append(final_name)
                    except Exception as err:
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
st.divider()

if st.checkbox("관리자 확인용", key="admin_open"):
    admin_pw = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
    if admin_pw and check_password(admin_pw, ADMIN_PASSWORD):
        flash = st.session_state.pop("admin_flash", None)
        if flash:
            st.success(flash)

        st.subheader("📋 제출 현황")

        try:
            items = list_items()
        except Exception as err:
            items = []
            st.error(f"목록을 불러오지 못했습니다: {err}")

        folder_id = st.session_state.get("drive_folder_id")
        if folder_id:
            st.link_button(
                "📁 구글 드라이브 폴더 열기",
                f"https://drive.google.com/drive/folders/{folder_id}",
            )

        if not items:
            st.info("아직 제출된 파일이 없습니다.")
        else:
            records = []
            for item in items:
                info = parse_name(item["name"]) or {
                    "name": "미분류",
                    "date": None,
                    "type": "미분류",
                }
                records.append((item, info))

            # ---- 활동 종류로 걸러내기 ----
            types_present = [
                t for t in ACTIVITY_TYPES + ["미분류"]
                if any(i["type"] == t for _, i in records)
            ]
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

            # ---- 묶는 기준 (활동일자 / 제출일시) ----
            has_created = any(i.get("created") for i, _ in records)
            basis = "활동일자"
            if has_created:
                basis = st.radio(
                    "묶는 기준",
                    options=["활동일자", "제출일시"],
                    horizontal=True,
                    key="admin_basis",
                    help="활동일자는 서포터즈가 고른 날짜, 제출일시는 실제로 올린 시각입니다.",
                )

            def basis_date(item, info):
                if basis == "제출일시":
                    return item["created"].date() if item.get("created") else None
                return info["date"]

            # ---- 주차로 묶기 ----
            weeks = {}
            for item, info in records:
                key = basis_date(item, info)
                weeks.setdefault(week_start(key) if key else None, []).append(
                    (item, info)
                )

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

            if basis == "제출일시":
                shown = sorted(
                    shown,
                    key=lambda r: (r[0].get("created") or datetime.min, r[1]["name"]),
                    reverse=True,
                )
            else:
                shown = sorted(
                    shown,
                    key=lambda r: (r[1]["date"] or date.min, r[1]["name"]),
                    reverse=True,
                )

            # ---- 선택한 주차 전체를 ZIP으로 (누를 때만 내려받음) ----
            if not has_created:
                st.caption(
                    "제출일시로 보려면 Apps Script 코드를 최신본으로 바꾸고 "
                    "다시 배포해야 합니다."
                )

            zip_key = f"zip::{picked}::{type_choice}::{basis}"
            if shown:
                if st.session_state.get("zip_ready") == zip_key:
                    st.download_button(
                        f"📦 {len(shown)}개 파일 받기 (ZIP)",
                        st.session_state["zip_data"],
                        file_name=f"활동일지_{picked.split(' · ')[0].replace(' ', '')}.zip",
                        mime="application/zip",
                    )
                elif st.button(f"📦 {len(shown)}개 파일 ZIP으로 묶기"):
                    try:
                        with st.spinner("파일을 모으는 중입니다…"):
                            buffer = io.BytesIO()
                            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                                for item, _ in shown:
                                    zf.writestr(item["name"], read_item(item))
                            st.session_state["zip_ready"] = zip_key
                            st.session_state["zip_data"] = buffer.getvalue()
                        st.rerun()
                    except Exception as err:
                        st.error(f"묶는 중 오류가 났습니다: {err}")

            st.divider()

            # ---- 목록 ----
            delete_mode = st.toggle(
                "🗑 삭제 모드 (잘못 올라온 파일 정리)", key="admin_delete_mode"
            )
            if delete_mode:
                st.caption(
                    "드라이브 휴지통으로 옮겨집니다. 30일 안에는 되살릴 수 있습니다."
                    if GAS
                    else "삭제한 파일은 복구할 수 없습니다."
                )
            pending = st.session_state.get("pending_delete")

            for item, info in shown:
                if delete_mode:
                    col1, col2, col3 = st.columns([3, 1, 1])
                else:
                    col1, col2 = st.columns([4, 1])
                    col3 = None

                col1.write(f"**{item['name']}**")
                detail = f"{item['size'] / 1024:,.0f}KB"
                if item.get("created"):
                    detail += f"  ·  제출 {item['created']:%Y-%m-%d %H:%M}"
                col1.caption(detail)

                if item["link"]:
                    col2.link_button("열기", item["link"])
                else:
                    col2.download_button(
                        "받기",
                        read_item(item),
                        file_name=item["name"],
                        key=f"dl_{item['id']}",
                    )

                if col3 is not None and col3.button("삭제", key=f"del_{item['id']}"):
                    st.session_state.pending_delete = item["id"]
                    st.rerun()

                # 삭제 전 한 번 더 확인
                if pending == item["id"]:
                    st.warning(f"'{item['name']}' 을(를) 삭제할까요?")
                    yes, no = st.columns(2)
                    if yes.button("네, 삭제합니다", key=f"yes_{item['id']}"):
                        try:
                            ok = delete_item(item)
                            msg = f"'{item['name']}' 을(를) 삭제했습니다."
                        except Exception as err:
                            ok, msg = False, f"삭제 실패: {err}"
                        st.session_state.admin_flash = msg if ok else msg
                        st.session_state.pending_delete = None
                        st.session_state.pop("zip_ready", None)
                        st.rerun()
                    if no.button("취소", key=f"no_{item['id']}"):
                        st.session_state.pending_delete = None
                        st.rerun()
    elif admin_pw:
        st.error("관리자 비밀번호가 다릅니다.")

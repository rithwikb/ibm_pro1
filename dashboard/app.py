import sys
import os
import time
import re
import subprocess
import glob
import json
import streamlit as st

# Sync Streamlit Cloud secrets to os.environ so pipeline subprocesses can access GROQ_API_KEY
if hasattr(st, "secrets"):
    try:
        for _k, _v in st.secrets.items():
            if isinstance(_v, (str, int, float, bool)) and _k not in os.environ:
                os.environ[_k] = str(_v)
    except Exception:
        pass

st.set_page_config(page_title="AI Dev Team", page_icon="🤖", layout="wide")


# VS Code Dark+ Theme CSS + Sidebar Styles
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Segoe+UI:wght@300;400;600&display=swap');
    
    html, body, [class*="css"] { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .stApp { background-color: #1e1e1e; color: #cccccc; }
    [data-testid="stSidebar"] { background-color: #000000 !important; border-right: 2px solid #ffffff !important; }
    [data-testid="stSidebar"] * { color: #ffffff; }
    
    .sidebar-new-btn > button { background-color: #222222 !important; border: 1px solid #ffffff !important; border-radius: 5px !important; margin-bottom: 20px; }
    .sidebar-new-btn > button:hover { background-color: #444444 !important; }
    
    .main-title { font-size: 2.2rem; font-weight: 600; color: #ffffff; margin-bottom: 5px; }
    .sub-title { font-size: 1rem; color: #9cdcfe; margin-bottom: 30px; }

    .stTextArea textarea, .stTextInput input {
        background-color: #252526 !important; border: 1px solid #3c3c3c !important;
        border-radius: 3px !important; color: #cccccc !important; padding: 10px !important; box-shadow: none !important;
    }
    .stTextArea textarea:focus, .stTextInput input:focus { border-color: #007fd4 !important; background-color: #2d2d2d !important; }
    label, .stMarkdown p { color: #cccccc !important; font-size: 0.9rem !important; font-weight: 400 !important; }
    
    /* Main Action Button */
    .primary-btn > div[data-testid="stButton"] > button {
        background-color: #0e639c !important; border: 1px solid transparent !important;
        border-radius: 2px !important; color: #ffffff !important; font-weight: 600 !important;
        padding: 10px 16px !important; transition: background-color 0.1s ease !important;
    }
    .primary-btn > div[data-testid="stButton"] > button:hover { background-color: #1177bb !important; }
    
    /* Approve Button */
    .approve-btn > div[data-testid="stButton"] > button {
        background-color: #238636 !important; border: 1px solid rgba(240,246,252,0.1) !important;
        color: #ffffff !important; font-weight: 600 !important;
    }
    .approve-btn > div[data-testid="stButton"] > button:hover { background-color: #2ea043 !important; }
    
    /* Reject Button */
    .reject-btn > div[data-testid="stButton"] > button {
        background-color: #da3633 !important; border: 1px solid rgba(240,246,252,0.1) !important;
        color: #ffffff !important; font-weight: 600 !important;
    }
    .reject-btn > div[data-testid="stButton"] > button:hover { background-color: #f85149 !important; }

    div[data-testid="stCodeBlock"] { background-color: #1e1e1e !important; border: 1px solid #454545 !important; border-radius: 3px !important; }
    div[data-testid="stCodeBlock"] code { color: #cccccc !important; font-family: Consolas, "Courier New", monospace !important; font-size: 0.9rem !important; }
    
    .approval-card {
        background-color: #252526; border: 1px solid #007fd4; border-radius: 5px;
        padding: 20px; margin-top: 20px; border-left: 5px solid #007fd4;
    }

    /* Generated Code Viewer Card */
    .code-viewer-card {
        background-color: #252526; border: 1px solid #007fd4; border-radius: 5px;
        padding: 20px; margin-top: 25px; border-left: 4px solid #007fd4;
    }
    .code-viewer-title {
        font-size: 1.15rem; font-weight: 600; color: #9cdcfe; margin-bottom: 12px;
    }

    /* Username input styling */
    .username-bar {
        background-color: #252526; border: 1px solid #3c3c3c; border-radius: 5px;
        padding: 8px 14px; margin-bottom: 10px;
    }
    .username-label {
        color: #9cdcfe !important; font-size: 0.85rem !important; font-weight: 600 !important;
        margin-bottom: 2px !important;
    }
</style>
""", unsafe_allow_html=True)

_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_DASHBOARD_DIR)
_TASKS_BASE_DIR = os.path.join(_DASHBOARD_DIR, "tasks")


# ---------------------------------------------------------------------------
# Helper: per-user tasks directory
# ---------------------------------------------------------------------------
def _get_user_tasks_dir(username: str) -> str:
    """Return the tasks directory for a specific user."""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', username.strip().lower())
    if not safe_name:
        safe_name = "default"
    return os.path.join(_TASKS_BASE_DIR, safe_name)


# ---------------------------------------------------------------------------
# Helper: extract generated Python code from unified diff
# ---------------------------------------------------------------------------
def _extract_generated_code(code_diff: str) -> dict:
    """
    Parse a unified diff string and extract the full generated source code
    for each file mentioned in the diff.
    
    Returns a dict mapping filename -> full source code string.
    """
    if not code_diff:
        return {}

    files = {}
    current_file = None
    current_lines = []

    for line in code_diff.split("\n"):
        # Detect file header: +++ b/filename.py
        if line.startswith("+++ "):
            # Save previous file if any
            if current_file and current_lines:
                files[current_file] = "\n".join(current_lines)
            # Extract filename from +++ b/filename or +++ b/path/to/file
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_file = path
            current_lines = []
            continue

        # Skip --- header lines and @@ hunk markers
        if line.startswith("--- ") or line.startswith("@@"):
            continue

        # Lines starting with '+' are added lines (the generated code)
        if line.startswith("+"):
            current_lines.append(line[1:])  # strip the leading '+'
        # Context lines (no prefix or space prefix) are also part of the code
        elif line.startswith(" "):
            current_lines.append(line[1:])  # strip the leading space
        # Lines starting with '-' are removed lines — skip them
        elif line.startswith("-"):
            continue
        # Blank lines in the diff that belong to code
        elif line == "":
            if current_file is not None:
                current_lines.append("")

    # Save last file
    if current_file and current_lines:
        files[current_file] = "\n".join(current_lines)

    # Strip trailing blank lines from each file
    for fname in files:
        files[fname] = files[fname].rstrip("\n") + "\n"

    return files


# ---------------------------------------------------------------------------
# Username management via query params + session state
# ---------------------------------------------------------------------------
if "username" not in st.session_state:
    # Try to restore from query params (survives page refresh)
    params = st.query_params
    if "user" in params:
        st.session_state.username = params["user"]
    else:
        st.session_state.username = ""

if "form_reset_counter" not in st.session_state:
    st.session_state.form_reset_counter = 0

# If no username yet, show a login prompt and stop
if not st.session_state.username:
    st.markdown("<div class='main-title'>🤖 AI DEV TEAM</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>A MULTI AGENT MODEL FOR DEALING WITH PYTHON PROJECTS</div>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### 👤 Enter your username to continue")
    st.caption("Your task history will be private to your username.")
    entered_name = st.text_input("Username", key="username_input", placeholder="e.g. rithwik, soumya, dev1", label_visibility="collapsed")
    if st.button("Continue →", use_container_width=False):
        if entered_name.strip():
            st.session_state.username = entered_name.strip()
            st.query_params["user"] = entered_name.strip()
            st.rerun()
        else:
            st.error("Please enter a username.")
    st.stop()

# At this point we have a valid username
current_username = st.session_state.username
# Ensure query param is in sync
st.query_params["user"] = current_username
_USER_TASKS_DIR = _get_user_tasks_dir(current_username)
os.makedirs(_USER_TASKS_DIR, exist_ok=True)


# --- SIDEBAR LOGIC ---
with st.sidebar:
    st.markdown("<h2>History</h2>", unsafe_allow_html=True)
    st.markdown(f"<p class='username-label'>👤 Logged in as: <b>{current_username}</b></p>", unsafe_allow_html=True)

    # Logout / switch user button
    if st.button("🔄 Switch User", use_container_width=True, key="switch_user"):
        st.session_state.username = ""
        st.session_state.selected_task_file = None
        if "user" in st.query_params:
            del st.query_params["user"]
        st.rerun()

    st.markdown('<div class="sidebar-new-btn">', unsafe_allow_html=True)
    if st.button("📝 New chat (Reset)", use_container_width=True):
        st.session_state.form_reset_counter += 1
        st.session_state.selected_task_file = None
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("### Recents")
    task_files = glob.glob(os.path.join(_USER_TASKS_DIR, "*.json"))
    task_files.sort(key=os.path.getctime, reverse=True)
    
    if not task_files:
        st.caption("No history yet.")
    else:
        for f in task_files:
            try:
                with open(f, "r", encoding="utf-8") as f_in:
                    data = json.load(f_in)
                t_id = data.get("task_id", os.path.basename(f))
                t_req = data.get("feature_request", "No description")
                t_status = data.get("status", "")
                
                # Truncate description for sidebar
                if len(t_req) > 35:
                    t_req = t_req[:32] + "..."
                    
                # 2-column layout for the history item and the delete button
                scol1, scol2 = st.columns([4, 1])
                with scol1:
                    icon = "👀" if t_status == "awaiting_human_approval" else "✅" if t_status in ("approved", "verified_fixed") else "📄"
                    if st.button(f"{icon} {t_id}\n{t_req}", key=f"sel_{t_id}", use_container_width=True):
                        st.session_state.selected_task_file = f
                        st.rerun()
                with scol2:
                    if st.button("🗑️", key=f"del_{t_id}"):
                        os.remove(f)
                        if st.session_state.get("selected_task_file") == f:
                            st.session_state.selected_task_file = None
                        st.rerun()
            except Exception as e:
                pass

# Load selected task data if present
loaded_req = ""
loaded_repo = "sample_repo/flaskbb"
loaded_tid = f"live_{int(time.time()) % 1000:03d}"
loaded_diff = None
loaded_plan = None

if st.session_state.get("selected_task_file") and os.path.exists(st.session_state.selected_task_file):
    try:
        with open(st.session_state.selected_task_file, "r", encoding="utf-8") as f_in:
            sel_data = json.load(f_in)
            loaded_req = sel_data.get("feature_request", "")
            loaded_tid = sel_data.get("task_id", "")
            loaded_diff = sel_data.get("code_diff")
            loaded_plan = sel_data.get("plan")
    except Exception:
        pass


import uuid as _uuid

# --- MAIN CONTENT ---
st.markdown("<div class='main-title'>🤖 AI DEV TEAM</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>A MULTI AGENT MODEL FOR DEALING WITH PYTHON PROJECTS</div>", unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)

key_suffix = st.session_state.form_reset_counter

# Fixed project directory (not user-editable)
target_repo = "sample_repo/flaskbb"

st.markdown("<p><b>ENTER REQUESTS:-</b></p>", unsafe_allow_html=True)
user_request = st.text_area("Request", key=f"req_{key_suffix}", label_visibility="collapsed", value=loaded_req, placeholder="create a testers.py that prints hi", height=140)

st.markdown("<div style='height: 25px;'></div>", unsafe_allow_html=True)

# Run Button
st.markdown('<div class="primary-btn">', unsafe_allow_html=True)
if st.button("🚀 RUN PIPELINE", use_container_width=True):
    # Auto-generate a unique task ID
    tid_clean = f"task_{_uuid.uuid4().hex[:6]}"
    
    if not user_request.strip():
        st.error("Please enter a feature request first.")
    else:
        st.info(f"Running pipeline for: *{user_request.strip()[:80]}...*")
        
        cmd = [
            sys.executable, "-u", "orchestration/pipeline.py",
            "--repo", target_repo,
            "--request", user_request.strip(),
            "--username", current_username,
            "--task-id", tid_clean,
        ]

        st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)
        log_box = st.empty()
        captured_lines = []

        try:
            proc = subprocess.Popen(
                cmd, cwd=_REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, encoding="utf-8", bufsize=1
            )
            for line in iter(proc.stdout.readline, ""):
                captured_lines.append(line)
                log_box.code("".join(captured_lines), language="text")
            proc.wait()
            
            if proc.returncode == 0:
                st.success("✅ Pipeline completed successfully! Refreshing UI...")
                time.sleep(1.5)
                st.session_state.selected_task_file = os.path.join(_USER_TASKS_DIR, f"{tid_clean}.json")
                st.rerun()
            else:
                st.error("❌ Pipeline finished with an error. Check the logs above.")

        except Exception as e:
            st.error(f"Failed to start pipeline: {e}")
            
st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Generated Code Viewer — shown for loaded historical tasks
# ---------------------------------------------------------------------------
def _render_code_viewer(code_diff, section_title="📄 Generated Code"):
    """Render the generated code viewer with tabs and download buttons."""
    generated_files = _extract_generated_code(code_diff)
    if not generated_files:
        st.info("No generated Python code to display for this task.")
        return

    st.markdown(f"<div class='code-viewer-title'>{section_title}</div>", unsafe_allow_html=True)

    if len(generated_files) == 1:
        fname, code = list(generated_files.items())[0]
        st.markdown(f"**`{fname}`**")
        st.code(code, language="python")
        st.download_button(
            label=f"⬇ Download {fname}",
            data=code,
            file_name=fname,
            mime="text/x-python",
            key=f"dl_{fname}_{hash(code) % 10000}",
        )
    else:
        tab_labels = [f"📄 {fname}" for fname in generated_files.keys()]
        tabs = st.tabs(tab_labels)
        for tab, (fname, code) in zip(tabs, generated_files.items()):
            with tab:
                st.code(code, language="python")
                st.download_button(
                    label=f"⬇ Download {fname}",
                    data=code,
                    file_name=fname,
                    mime="text/x-python",
                    key=f"dl_{fname}_{hash(code) % 10000}",
                )


# Display historical data if selected
if loaded_diff or loaded_plan:
    st.markdown("---")
    st.markdown(f"### 🕒 History for Task: `{loaded_tid}`")
    
    tab1, tab2, tab3 = st.tabs(["📄 Generated Code", "💻 Code Diff", "📋 Architect Plan"])
    
    with tab1:
        if loaded_diff:
            _render_code_viewer(loaded_diff, section_title="Generated Python Code")
        else:
            st.info("No generated code available for this task.")

    with tab2:
        if loaded_diff:
            st.code(loaded_diff, language="diff")
        else:
            st.info("No code changes were generated for this task.")
            
    with tab3:
        if loaded_plan:
            st.markdown(loaded_plan)
        else:
            st.info("No architectural plan was generated.")

# --- HUMAN APPROVAL SECTION ---
pending_tasks = []
for f in task_files:
    try:
        with open(f, "r", encoding="utf-8") as f_in:
            data = json.load(f_in)
            if data.get("status") == "awaiting_human_approval" and data.get("code_diff"):
                pending_tasks.append((f, data))
    except Exception:
        pass

@st.dialog("👀 Action Required: Human Approval")
def review_modal(f_path, t_data):
    tid = t_data.get("task_id", "Unknown")
    freq = t_data.get("feature_request", "")
    diff = t_data.get("code_diff", "")
    
    st.markdown(f"**Task:** {tid}")
    st.markdown(f"**Request:** *{freq}*")
    st.code(diff, language="diff")
    
    st.markdown("---")
    acol1, acol2 = st.columns(2)
    with acol1:
        st.markdown('<div class="approve-btn">', unsafe_allow_html=True)
        if st.button("✅ Approve & Apply", key=f"approve_{tid}", use_container_width=True):
            t_data["status"] = "approved"
            with open(f_path, "w", encoding="utf-8") as f_out:
                json.dump(t_data, f_out, indent=2)
            
            with st.spinner(f"Applying fixes for {tid}..."):
                apply_cmd = [sys.executable, "orchestration/apply_fixes.py", "--repo", target_repo]
                try:
                    res = subprocess.run(apply_cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
                    if res.returncode == 0:
                        st.success("Changes applied successfully!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"Failed to apply fixes:\n{res.stderr}")
                except Exception as e:
                    st.error(f"Error executing apply_fixes.py: {e}")
        st.markdown('</div>', unsafe_allow_html=True)
        
    with acol2:
        st.markdown('<div class="reject-btn">', unsafe_allow_html=True)
        if st.button("❌ Reject", key=f"reject_{tid}", use_container_width=True):
            t_data["status"] = "rejected"
            with open(f_path, "w", encoding="utf-8") as f_out:
                json.dump(t_data, f_out, indent=2)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

if pending_tasks:
    # Pop up the dialog for the first pending task automatically
    f_path, t_data = pending_tasks[0]
    review_modal(f_path, t_data)

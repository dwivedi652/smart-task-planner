
from __future__ import annotations
import html, sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

DB = Path(__file__).with_name("tasks.db")
STATUSES = ["Pending", "Ongoing", "On hold", "Completed"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
STATUS_COLOUR = {"Pending":"#F4B400","Ongoing":"#1A73E8","On hold":"#7E57C2","Completed":"#34A853"}
PRIORITY_COLOUR = {"Critical":"#D93025","High":"#F4511E","Medium":"#F9AB00","Low":"#5F6368"}
PRIORITY_WEIGHT = {"Critical":4,"High":3,"Medium":2,"Low":1}

st.set_page_config(page_title="Smart Task Planner", page_icon="✅", layout="wide")

def connect():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS tasks(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL, project TEXT DEFAULT '', description TEXT DEFAULT '',
          priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Pending',
          start_date TEXT, end_date TEXT, estimated_hours REAL DEFAULT 1,
          progress INTEGER DEFAULT 0, delay_reason TEXT DEFAULT '', notes TEXT DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)""")
        con.commit()

def load_tasks():
    with connect() as con:
        df = pd.read_sql_query("SELECT * FROM tasks ORDER BY updated_at DESC", con)
    if df.empty:
        return df
    for c in ["start_date","end_date","created_at","updated_at","completed_at"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["estimated_hours"] = pd.to_numeric(df["estimated_hours"], errors="coerce").fillna(0)
    df["progress"] = pd.to_numeric(df["progress"], errors="coerce").fillna(0).astype(int)
    return df

def save_task(values, task_id=None):
    now = datetime.now().isoformat(timespec="seconds")
    completed_at = now if values["status"] == "Completed" else None
    with connect() as con:
        if task_id is None:
            con.execute("""INSERT INTO tasks(
              title,project,description,priority,status,start_date,end_date,
              estimated_hours,progress,delay_reason,notes,created_at,updated_at,completed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (values["title"],values["project"],values["description"],values["priority"],
               values["status"],values["start_date"],values["end_date"],values["estimated_hours"],
               values["progress"],values["delay_reason"],values["notes"],now,now,completed_at))
        else:
            old = con.execute("SELECT completed_at FROM tasks WHERE id=?", (task_id,)).fetchone()
            if values["status"] == "Completed" and old and old["completed_at"]:
                completed_at = old["completed_at"]
            con.execute("""UPDATE tasks SET title=?,project=?,description=?,priority=?,status=?,
              start_date=?,end_date=?,estimated_hours=?,progress=?,delay_reason=?,notes=?,
              updated_at=?,completed_at=? WHERE id=?""",
              (values["title"],values["project"],values["description"],values["priority"],
               values["status"],values["start_date"],values["end_date"],values["estimated_hours"],
               values["progress"],values["delay_reason"],values["notes"],now,completed_at,task_id))
        con.commit()

def remove_task(task_id):
    with connect() as con:
        con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        con.commit()

def working_day(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d

def score(row, today):
    due = 0
    if pd.notna(row["end_date"]):
        days = (row["end_date"].date() - today).days
        due = 70 if days < 0 else 45 if days <= 3 else 25 if days <= 7 else 10 if days <= 14 else 0
    status = {"Ongoing":15,"Pending":8,"On hold":-8,"Completed":-999}.get(row["status"],0)
    remaining = max(.25, row["estimated_hours"] * (1-row["progress"]/100))
    return round(PRIORITY_WEIGHT.get(row["priority"],2)*25 + due + status + (100-row["progress"])*.12 - min(remaining,40)*.2,1)

def smart_plan(df, capacity, start):
    if df.empty:
        return pd.DataFrame()
    active = df[df["status"]!="Completed"].copy()
    if active.empty:
        return pd.DataFrame()
    active["score"] = active.apply(lambda r: score(r,start), axis=1)
    active["remaining"] = (active["estimated_hours"]*(1-active["progress"].clip(0,100)/100)).clip(lower=.25)
    active = active.sort_values(["score","end_date"], ascending=[False,True], na_position="last")
    current = working_day(start)
    available = capacity
    out = []
    for _,r in active.iterrows():
        left = float(r["remaining"])
        suggested_start = current
        last = current
        while left > 0:
            if current.weekday() >= 5:
                current = working_day(current)
                available = capacity
            allocation = min(left, available)
            left -= allocation
            available -= allocation
            last = current
            if left > 0 and available <= .001:
                current = working_day(current + timedelta(days=1))
                available = capacity
        if available <= .001:
            current = working_day(current + timedelta(days=1))
            available = capacity
        deadline = r["end_date"].date() if pd.notna(r["end_date"]) else None
        risk = "Likely delayed" if deadline and last > deadline else "At risk" if deadline and (deadline-last).days <= 1 else "On track"
        out.append({"id":int(r["id"]),"Task":r["title"],"Project":r["project"] or "General",
                    "Priority":r["priority"],"Status":r["status"],"Progress":int(r["progress"]),
                    "Remaining hours":round(float(r["remaining"]),1),"Urgency score":r["score"],
                    "Suggested start":suggested_start,"Suggested finish":last,
                    "Current deadline":deadline,"Delivery risk":risk})
    return pd.DataFrame(out)

def apply_plan(plan):
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        for _,r in plan.iterrows():
            con.execute("UPDATE tasks SET start_date=?,end_date=?,updated_at=? WHERE id=?",
                        (r["Suggested start"].isoformat(),r["Suggested finish"].isoformat(),now,int(r["id"])))
        con.commit()

def weekly_report(df, plan, week_start):
    week_end = week_start + timedelta(days=6)
    if df.empty:
        report = pd.DataFrame()
    else:
        active = df[df["status"]!="Completed"].copy()
        ids = set()
        for _,r in active.iterrows():
            due = r["end_date"].date() if pd.notna(r["end_date"]) else None
            if due and due <= week_end:
                ids.add(int(r["id"]))
        if not plan.empty:
            for _,r in plan.iterrows():
                if week_start <= r["Suggested finish"] <= week_end:
                    ids.add(int(r["id"]))
        src = active[active["id"].astype(int).isin(ids)].copy()
        lookup = plan.set_index("id")["Suggested finish"].to_dict() if not plan.empty else {}
        report = pd.DataFrame({
            "Task":src["title"],"Project":src["project"].replace("","General"),
            "Priority":src["priority"],"Status":src["status"],
            "Progress":src["progress"].astype(str)+"%",
            "Deadline":src["end_date"].dt.strftime("%d %b %Y").fillna("Not set"),
            "Planned finish":src["id"].astype(int).map(lookup).apply(lambda x:x.strftime("%d %b %Y") if isinstance(x,date) else "Not planned"),
            "Comment":src["delay_reason"].where(src["delay_reason"].str.strip()!="",src["notes"])
        })
    rows = ""
    if not report.empty:
        for _,r in report.iterrows():
            rows += "<tr>"+"".join(f"<td>{html.escape(str(r[c]))}</td>" for c in report.columns)+"</tr>"
    headers = "".join(f"<th>{html.escape(c)}</th>" for c in report.columns)
    report_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body{{font-family:Arial;margin:32px;color:#202124}} h1{{color:#174EA6}}
    table{{border-collapse:collapse;width:100%;font-size:13px}} th{{background:#E8F0FE}}
    th,td{{border:1px solid #DADCE0;padding:8px;text-align:left;vertical-align:top}}
    </style></head><body><h1>Weekly Task Report</h1>
    <p>{week_start.strftime("%d %B %Y")} to {week_end.strftime("%d %B %Y")}</p>
    <p><strong>{len(report)}</strong> task(s) require attention.</p>
    <table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></body></html>"""
    return report, report_html, week_end

def values_from_form(prefix, existing=None):
    ex = existing
    c1,c2 = st.columns(2)
    with c1:
        title = st.text_input("Task title *", value="" if ex is None else str(ex["title"]), key=f"{prefix}_title")
        project = st.text_input("Project or workstream", value="" if ex is None else str(ex["project"] or ""), key=f"{prefix}_project")
        description = st.text_area("Description", value="" if ex is None else str(ex["description"] or ""), key=f"{prefix}_description")
        priority = st.selectbox("Importance", PRIORITIES, index=1 if ex is None else PRIORITIES.index(ex["priority"]), key=f"{prefix}_priority")
        status = st.selectbox("Status", STATUSES, index=0 if ex is None else STATUSES.index(ex["status"]), key=f"{prefix}_status")
    with c2:
        sd = date.today() if ex is None or pd.isna(ex["start_date"]) else ex["start_date"].date()
        ed = date.today()+timedelta(days=7) if ex is None or pd.isna(ex["end_date"]) else ex["end_date"].date()
        start = st.date_input("Start date", value=sd, key=f"{prefix}_start")
        end = st.date_input("Target end date", value=ed, key=f"{prefix}_end")
        hours = st.number_input("Estimated time needed (hours)", .5, 1000., 4. if ex is None else float(ex["estimated_hours"]), .5, key=f"{prefix}_hours")
        progress = st.slider("Progress",0,100,0 if ex is None else int(ex["progress"]),5,key=f"{prefix}_progress")
        delay = st.text_area("Reason for delay or blocker", value="" if ex is None else str(ex["delay_reason"] or ""), key=f"{prefix}_delay")
        notes = st.text_area("Comments or next action", value="" if ex is None else str(ex["notes"] or ""), key=f"{prefix}_notes")
    if status=="Completed":
        progress=100
    return {"title":title.strip(),"project":project.strip(),"description":description.strip(),
            "priority":priority,"status":status,"start_date":start.isoformat(),"end_date":end.isoformat(),
            "estimated_hours":hours,"progress":progress,"delay_reason":delay.strip(),"notes":notes.strip()}, start, end

ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

def require_admin():
    pw = ADMIN_PASSWORD
    # If no password is configured, treat everyone as admin
    if not pw:
        if "is_admin" not in st.session_state:
            st.session_state["is_admin"] = True
        return True

    if "is_admin" not in st.session_state:
        st.session_state["is_admin"] = False

    if st.session_state["is_admin"]:
        return True

    # Owner login section in the sidebar
    with st.sidebar:
        st.subheader("Owner access")
        entered = st.text_input("Admin password", type="password", key="admin_pw")
        if st.button("Unlock editing", key="admin_btn"):
            if entered == pw:
                st.session_state["is_admin"] = True
                st.success("Editing unlocked for this session.")
            else:
                st.error("Incorrect password.")

    return st.session_state["is_admin"]


init_db()
st.markdown("""<style>
.block-container{padding-top:1.2rem}.card{border:1px solid #DADCE0;border-radius:13px;padding:13px;margin-bottom:10px;background:white}
.badge{color:white;padding:3px 8px;border-radius:99px;font-size:.72rem;font-weight:700}
.small{color:#5F6368;font-size:.8rem}.boardhead{color:white;padding:8px;border-radius:9px;text-align:center;font-weight:800}
</style>""", unsafe_allow_html=True)

st.title("✅ Smart Task Planner")
st.caption("Record, prioritise, reorganise and report your work from one interactive platform.")

is_admin = require_admin()

with st.sidebar:
    st.header("Planning settings")
    capacity = st.number_input("Available hours per working day", .5, 16., 6., .5)
    plan_from = st.date_input("Reorganise from", date.today())
    st.divider()
    st.caption("Tasks are stored locally in tasks.db.")

tasks = load_tasks()
plan = smart_plan(tasks, capacity, plan_from)
tabs = st.tabs(["Dashboard","Add task","Kanban","Smart planner","Weekly report","Manage tasks"])

with tabs[0]:
    active = tasks[tasks["status"]!="Completed"] if not tasks.empty else tasks
    today = date.today()
    overdue = int(active["end_date"].apply(lambda x:pd.notna(x) and x.date()<today).sum()) if not active.empty else 0
    due7 = int(active["end_date"].apply(lambda x:pd.notna(x) and today<=x.date()<=today+timedelta(days=7)).sum()) if not active.empty else 0
    m1,m2,m3,m4,m5=st.columns(5)
    m1.metric("Active",len(active)); m2.metric("Ongoing",int((tasks["status"]=="Ongoing").sum()) if not tasks.empty else 0)
    m3.metric("Due in 7 days",due7); m4.metric("Overdue",overdue); m5.metric("Completed",int((tasks["status"]=="Completed").sum()) if not tasks.empty else 0)
    st.subheader("Priority focus")
    if plan.empty: st.info("Add a task to create your priority queue.")
    else: st.dataframe(plan.head(10),use_container_width=True,hide_index=True)
    if not tasks.empty:
        c1,c2=st.columns(2)
        with c1:
            d=tasks.groupby("status").size().reset_index(name="Tasks")
            fig=px.pie(d,values="Tasks",names="status",hole=.5,color="status",color_discrete_map=STATUS_COLOUR,title="Tasks by status")
            st.plotly_chart(fig,use_container_width=True)
        with c2:
            d=active.groupby("priority").size().reset_index(name="Tasks") if not active.empty else pd.DataFrame({"priority":[],"Tasks":[]})
            fig=px.bar(d,x="priority",y="Tasks",color="priority",color_discrete_map=PRIORITY_COLOUR,title="Active work by importance")
            st.plotly_chart(fig,use_container_width=True)

with tabs[1]:
    st.subheader("Add a new task")
    if not is_admin:
        st.info("Only the owner can add tasks. Viewing is read-only for guests.")
    else:
        with st.form("add_form",clear_on_submit=True):
            vals,sd,ed=values_from_form("add")
            submitted=st.form_submit_button("Add task",use_container_width=True)
            if submitted:
                if not vals["title"]: st.error("Please enter a task title.")
                elif ed<sd: st.error("The target end date cannot be earlier than the start date.")
                else:
                    save_task(vals); st.success("Task added and the plan has been reorganised."); st.rerun()

with tabs[2]:
    st.subheader("Colour coded Kanban board")
    if tasks.empty: st.info("No tasks available.")
    else:
        project_list=["All"]+sorted([x for x in tasks["project"].dropna().unique() if x])
        selected_project=st.selectbox("Project",project_list)
        board=tasks if selected_project=="All" else tasks[tasks["project"]==selected_project]
        cols=st.columns(4)
        for col,status in zip(cols,STATUSES):
            with col:
                st.markdown(f'<div class="boardhead" style="background:{STATUS_COLOUR[status]}">{status}</div>',unsafe_allow_html=True)
                subset=board[board["status"]==status]
                if subset.empty: st.caption("No tasks")
                for _,r in subset.iterrows():
                    due=r["end_date"].strftime("%d %b %Y") if pd.notna(r["end_date"]) else "Not set"
                    delay=f"<br><b>Delay:</b> {html.escape(str(r['delay_reason']))}" if str(r["delay_reason"]).strip() else ""
                    card_html = f"""<div class="card"><b>{html.escape(str(r["title"]))}</b><br>
                    <span class="small">{html.escape(str(r["project"] or "General"))}</span><br><br>
                    <span class="badge" style="background:{PRIORITY_COLOUR[r["priority"]]}">{r["priority"]}</span>
                    <p class="small">Due: {due}<br>Progress: {int(r["progress"])}%<br>
                    Estimate: {float(r["estimated_hours"]):.1f} h{delay}</p></div>"""
                    st.markdown(card_html,unsafe_allow_html=True)

with tabs[3]:
    st.subheader("Automatic workload reorganisation")
    st.caption("Ranking uses importance, deadline, current status, progress and remaining effort.")
    if plan.empty: st.info("There are no active tasks to plan.")
    else:
        c1,c2,c3=st.columns(3)
        total=plan["Remaining hours"].sum()
        c1.metric("Remaining workload",f"{total:.1f} h"); c2.metric("Weekly capacity",f"{capacity*5:.1f} h"); c3.metric("Equivalent weeks",f"{total/(capacity*5):.1f}")
        st.dataframe(plan,use_container_width=True,hide_index=True)
        fig=px.timeline(plan,x_start="Suggested start",x_end="Suggested finish",y="Task",color="Priority",color_discrete_map=PRIORITY_COLOUR,hover_data=["Project","Remaining hours","Delivery risk"])
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig,use_container_width=True)
        st.warning("Applying this plan replaces the start and end dates of all active tasks.")
        if not is_admin:
            st.info("Only the owner can apply suggested dates. Guests can view the plan but cannot change task dates.")
        else:
            if st.button("Apply suggested dates",type="primary"):
                apply_plan(plan); st.success("Suggested dates applied."); st.rerun()

with tabs[4]:
    st.subheader("Upcoming weekly meeting report")
    next_monday=date.today()+timedelta(days=(7-date.today().weekday())%7 or 7)
    week_start=st.date_input("Week starting",next_monday,key="week")
    report,report_html,week_end=weekly_report(tasks,plan,week_start)
    st.caption(f"{week_start.strftime('%d %B %Y')} to {week_end.strftime('%d %B %Y')}")
    if report.empty: st.info("No tasks need attention in this period.")
    else: st.dataframe(report,use_container_width=True,hide_index=True)
    lines=[f"WEEKLY TASK REPORT: {week_start.strftime('%d %B %Y')} to {week_end.strftime('%d %B %Y')}",""]
    if report.empty: lines.append("No tasks currently scheduled.")
    else:
        for _,r in report.iterrows():
            lines.append(f"• {r['Task']} | {r['Project']} | {r['Priority']} | {r['Status']} | {r['Progress']} | Deadline: {r['Deadline']} | Comment: {r['Comment']}")
    st.text_area("Copyable meeting summary","\n".join(lines),height=260)
    d1,d2=st.columns(2)
    d1.download_button("Download HTML report",report_html.encode(),f"weekly_report_{week_start}.html","text/html",use_container_width=True)
    d2.download_button("Download CSV report",report.to_csv(index=False).encode(),f"weekly_report_{week_start}.csv","text/csv",use_container_width=True)

with tabs[5]:
    st.subheader("Edit, complete or delete tasks")
    if tasks.empty:
        st.info("No tasks available.")
    elif not is_admin:
        st.info("Only the owner can edit or delete tasks. Guests have read-only access.")
    else:
        options={f"#{int(r['id'])} · {r['title']}":int(r["id"]) for _,r in tasks.iterrows()}
        label=st.selectbox("Select a task",list(options))
        task_id=options[label]
        existing=tasks[tasks["id"]==task_id].iloc[0]
        with st.form("edit_form"):
            vals,sd,ed=values_from_form("edit",existing)
            update=st.form_submit_button("Save changes",use_container_width=True)
            if update:
                if not vals["title"]: st.error("Please enter a task title.")
                elif ed<sd: st.error("The target end date cannot be earlier than the start date.")
                else:
                    save_task(vals,task_id); st.success("Task updated and the plan has been reorganised."); st.rerun()
        with st.expander("Delete task"):
            confirm=st.checkbox("I understand that this cannot be undone.")
            if st.button("Delete selected task",disabled=not confirm):
                remove_task(task_id); st.success("Task deleted."); st.rerun()

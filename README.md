# Smart Task Planner

## Features

• Task title, project, start date, deadline and estimated hours  
• Progress, priority, status, delay reason and comments  
• Colour coded Kanban board  
• Automatic workload reorganisation when tasks change  
• Suggested start and finish dates based on daily capacity  
• Timeline, workload dashboard and delivery risk  
• Upcoming weekly report as HTML, CSV and copyable text  
• Local SQLite database for persistent storage  

## Run in Windows PowerShell

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

When using a quoted Python path, PowerShell needs the call operator:

```powershell
& "C:\path\to\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& "C:\path\to\.venv\Scripts\python.exe" -m streamlit run app.py
```

The app creates `tasks.db` in the same folder. Keep this file when backing up or moving the platform.

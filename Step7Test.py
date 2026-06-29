from corporate_events.event_calendar import EventCalendar
from core.database import Database

db = Database()
cal = EventCalendar(db)
print(cal.get_summary("TCS").to_agent_text())
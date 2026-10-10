"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional , Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field, model_validator
from pymongo import MongoClient
from pymongo.errors import PyMongoError , DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event('startup')
def create_indexes():
    db.employees.create_index("emp_code", unique = True)
    db.attendance_logs.create_index([("emp_code",1),("date",1)], unique = True)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str , attendance_date : str) -> int:
    punch_in = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    shift_date = date.fromisoformat(attendance_date)
    shift_start_date = datetime(shift_date.year,
                                shift_date.month,
                                shift_date.day,
                                h,
                                m,
                                tzinfo=IST)
    elapsed_seconds = int((punch_in - shift_start_date).total_seconds() / 60)

    if elapsed_seconds <= 600:
        return 0
    return int(elapsed_seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern= r"^EMP\d{4,6}$")
    name: str = Field(min_length= 1, max_length= 100)
    email: str = Field(max_length=120,pattern= r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length= 1, max_length= 50)
    shift_start: str = Field(default="09:30", pattern= r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern= r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: date

    @model_validator(mode = 'after')
    def validate_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self

# Response Model for Create_employee
class EmployeeOut(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str
    created_at : int

class PunchInIn(BaseModel):
    emp_code: str = Field(pattern= r"^EMP\d{4,6}$")
    punched_at: Optional[int] = None
    status: Literal["PRESENT","WFH","ON_DUTY"] = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", response_model=EmployeeOut,status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump(mode='json')
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc['created_at'] = int(doc['created_at'].timestamp() * 1000)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0})
                 .sort("emp_code", 1)
                 .skip(skip)
                 .limit(page_size))
    for item in items:
        item['created_at'] = int(item['created_at'].timestamp() *1000)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404 , 'Employee not found')
    ts = (datetime.fromtimestamp(body.punched_at / 1000, tz= timezone.utc) 
          if body.punched_at is not None 
          else datetime.now(timezone.utc)).replace(microsecond=0)

    d = ts.astimezone(IST).date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"],d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, 'Employee already punched_in today')
    doc.pop('_id',None)
    doc['punch_in'] = int(doc['punch_in'].timestamp() * 1000)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    docs = db.attendance_logs.find(q).sort([("date", -1),('emp_code',1)])
    total = db.attendance_logs.count_documents(q)
    page_docs = list(docs.skip((page -1) * page_size).limit(page_size))
    for d in page_docs:
        d.pop("_id",None)
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

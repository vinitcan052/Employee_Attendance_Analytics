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
from fastapi import FastAPI, HTTPException ,Query
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
    elapsed_seconds = int((punch_in - shift_start_date).total_seconds())

    if elapsed_seconds <= 600:
        return 0
    return int(elapsed_seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    overtime = max(0, int((punch_out - end).total_seconds() / 60))

    if overtime < 30:
        return 0

    return overtime


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
    punched_at: Optional[int] = Field(default= None , le= 4102444800000, ge= 100000000000, strict= True)
    status: Literal["PRESENT","WFH","ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str = Field(pattern= r"^EMP\d{4,6}$")
    punched_at: Optional[int] = Field(default= None , le= 4102444800000, ge= 100000000000, strict= True)


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
def list_employees(department: Optional[str] = None,
                    page: int = Query(default= 1, ge=1),
                    page_size: int = Query(default= 20, ge=1, le= 100)):
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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default= 20, ge = 1, le = 100),
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


@app.post("/attendance/punch-out",status_code= 200)
def punch_out(body : PunchOutIn):
    emp = db.employees.find_one({"emp_code" : body.emp_code})

    if emp is None:
        raise HTTPException(404, "Employee Not Found")

    punched_at = (datetime.fromtimestamp(body.punched_at / 1000, tz = timezone.utc)
                  if body.punched_at is not None 
                  else datetime.now(timezone.utc)).replace(microsecond = 0)
    

    record = db.attendance_logs.find_one({"emp_code" : body.emp_code,
                                          "punch_in" : {"$ne" : None,"$lte" : punched_at}},
                                          sort= [("punch_in", -1)])

    if record is None:
            raise HTTPException(404, "No open punch-in found")

    if record['punch_out'] is not None:
        raise HTTPException(409, "Attendance record already punched out")

    punch_in = record['punch_in']

    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo = timezone.utc)

    if punched_at <= punch_in:
        raise HTTPException(422, 'Punch-out must be after punch-in')

    if punched_at - punch_in > timedelta(hours = 24):
        raise HTTPException(422, "Punch-out cannot be more than 24 hours after punch-in")

    work_hours = compute_work_hours(punch_in,punched_at)
    overtime_minutes = compute_overtime(punched_at,emp['shift_end'],record['date'])

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one({"_id": record["_id"],"punch_out" : None},
                                  {"$set" : {
                                      "punch_out" : punched_at,
                                      "work_hours" : work_hours,
                                      "overtime_minutes" :overtime_minutes,
                                      "half_day" : half_day
                                      }
                                  })

    if result.modified_count == 0:
        raise HTTPException(409, "Attendance record already punched out")

    updated_record = db.attendance_logs.find_one({"_id" : record["_id"]})
    updated_record.pop("_id", None)

    for field in ('punch_in', "punch_out"):
        value = updated_record[field]

        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo = timezone.utc)
                                                                      
            updated_record[field] = int(value.timestamp() * 1000)

    return updated_record

@app.patch("/attendance/{emp_code}/{date}",status_code= 200)
def attendance():
    pass

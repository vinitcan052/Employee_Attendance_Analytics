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
from decimal import Decimal, ROUND_HALF_UP

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

class RegularizeIn(BaseModel):
    status : Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in : Optional[int] = Field(default = None , le = 4102444800000, ge = 100000000000, strict = True)
    punch_out : Optional[int] = Field(default = None , le = 4102444800000, ge = 100000000000, strict = True)
    reason : str = Field(min_length= 5, max_length= 200)
    regularized_by : str = Field(min_length= 5, max_length= 50)


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

#   POST  /attendance/punch-out
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


#   PATCH /attendance/{emp_code}/{date}
@app.patch("/attendance/{emp_code}/{date}",status_code= 200)
def attendance(emp_code : str,
               date : str,
               body: RegularizeIn):

    try:
        attendance_date = datetime.strptime(date, "%Y-%m-%d").date()

        if attendance_date.isoformat() != date:
            raise ValueError()

    except ValueError:
        raise HTTPException(422, "Date must be in YYYY-MM-DD format")

    employee = db.employees.find_one({"emp_code" : emp_code})

    if employee is None:
        raise HTTPException(404 , "Employee not found")

    record = db.attendance_logs.find_one({"emp_code" : emp_code,
                                          "date" : date})

    if record is None :
        raise HTTPException(404 , "Attendance record not found")

    if "punch_in" in body.model_fields_set and body.punch_in is None:
        raise HTTPException(422, "punch_in cannot be null")

    if "punch_out" in body.model_fields_set and body.punch_out is None:
        raise HTTPException(422, "punch_out cannot be null")

    updates = body.model_dump(exclude_unset = True,
                              exclude = {"reason", "regularized_by"})

    if not updates:
        raise HTTPException(422, "At least one attendance field must be provided")

    if updates.get('status') in ('ABSENT', "LEAVE"):
        if "punch_in" in updates or "punch_out" in updates:
            raise HTTPException(422, "ABSENT or LEAVE cannot be set with punch times")

    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")

    if punch_in is not None:
        punch_in = punch_in.replace(tzinfo = timezone.utc) if punch_in.tzinfo is None else punch_in
        punch_in = punch_in.replace(microsecond = 0)

    if punch_out is not None:
        punch_out = punch_out.replace(tzinfo = timezone.utc) if punch_out.tzinfo is None else punch_out
        punch_out = punch_out.replace(microsecond = 0)

    if "punch_in" in updates:
        punch_in = datetime.fromtimestamp(updates["punch_in"]/1000, tz = timezone.utc)
        punch_in = punch_in.replace(microsecond = 0)

    if "punch_out" in updates:
        punch_out = datetime.fromtimestamp(updates["punch_out"]/1000, tz = timezone.utc)
        punch_out = punch_out.replace(microsecond = 0)

    final_status = updates.get("status", record["status"])

    if final_status in ("ABSENT", "LEAVE"):
        if "punch_in" in updates or "punch_out" in updates:
            raise HTTPException(422, "ABSENT or LEAVE cannot be set with punch times")

        punch_in = None
        punch_out = None

    if punch_in is not None and punch_out is not None:
        if punch_out <= punch_in:
            raise HTTPException(422, "punch_out must be after punch_in")

        if punch_out - punch_in > timedelta(hours= 24):
            raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    if final_status in ("PRESENT", "WFH", "ON_DUTY") and punch_in is None:
        raise HTTPException(422, "A punch_in is required for presence statuses")

    ist = timezone(timedelta(hours =5 , minutes = 30))

    if punch_in is not None:
        punch_in_ist = punch_in.astimezone(ist)

        expected_date = punch_in_ist.date()

        shift_start = employee["shift_start"]
        shift_end = employee["shift_end"]

        if shift_end <= shift_start and punch_in_ist.strftime("%H:%M") < shift_end:
            expected_date = expected_date - timedelta(days = 1)

        if expected_date.isoformat() != date:
            raise HTTPException(422, "punch_in must fall on the attendance date in IST")

    final_values = {
        "status" : final_status,
        "punch_in" : punch_in,
        "punch_out" : punch_out
    }

    late_minutes = 0
    work_hours = None
    overtime_minutes = 0
    half_day = False

    if punch_in is not None:
        late_minutes = compute_late_minutes(
            punch_in,
            employee["shift_start"],
            date
        )

    if punch_in is not None and punch_out is not None:
        from decimal import Decimal, ROUND_HALF_UP

        seconds = Decimal(str((punch_out - punch_in).total_seconds()))
        work_hours = float((seconds / Decimal("3600")).quantize(
            Decimal("0.01"), rounding = ROUND_HALF_UP
        ))

        shift_start = employee["shift_start"]
        shift_end = employee["shift_end"]

        shift_date = attendance_date
        punch_in_ist = punch_in.astimezone(ist)

        if shift_end <= shift_start and punch_in_ist.strftime("%H:%M") < shift_end:
            shift_date = shift_date + timedelta(days = 1)

        shift_end_hour, shift_end_minute = map(int, shift_end.split(":"))

        shift_end_time = datetime(
            shift_date.year,
            shift_date.month,
            shift_date.day,
            shift_end_hour,
            shift_end_minute,
            tzinfo = ist
        )

        overtime_elapsed = int(
            (punch_out.astimezone(ist) - shift_end_time).total_seconds() // 60
        )

        if overtime_elapsed >= 30:
            overtime_minutes = overtime_elapsed

        half_day = work_hours < 4.50

    final_values.update({
        "late_minutes" : late_minutes,
        "work_hours" : work_hours,
        "overtime_minutes" : overtime_minutes,
        "half_day" : half_day
    })

    changes = {}

    for field in ("status", "punch_in", "punch_out",
                  "work_hours", "late_minutes", "overtime_minutes", "half_day"):

        old_value = record.get(field)

        if field in ("punch_in", "punch_out") and old_value is not None:
            if old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo = timezone.utc)

            old_value = old_value.replace(microsecond = 0)

        if field in ("late_minutes", "overtime_minutes") and old_value is None:
            old_value = 0

        if field == "half_day" and old_value is None:
            old_value = False

        new_value = final_values[field]

        if old_value != new_value:
            changes[field] = {
                "from" : old_value,
                "to" : new_value
            }

    if not changes:
        raise HTTPException(422, "The request does not change any attendance fields")

    history_entry = {
        "at" : datetime.now(timezone.utc),
        "by" : body.regularized_by,
        "reason" : body.reason,
        "changes" : changes
    }

    result = db.attendance_logs.update_one(
        {
            "_id" : record["_id"],
            "history" : record.get("history", [])
        },
        {
            "$set" : final_values,
            "$push" : {"history" : history_entry}
        }
    )

    if result.modified_count == 0:
        raise HTTPException(409 , "Attendance record was changed by another request")

    updated_record = db.attendance_logs.find_one({"_id" : record["_id"]})

    updated_record.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        if updated_record[field] is not None:
            updated_record[field] = int(updated_record[field].replace(
                tzinfo = timezone.utc
            ).timestamp() * 1000)

    for entry in updated_record["history"]:
        entry["at"] = int(entry["at"].replace(
            tzinfo = timezone.utc
        ).timestamp() * 1000)

        for field, change in entry["changes"].items():
            if field in ("punch_in", "punch_out"):
                for key in ("from", "to"):
                    value = change[key]

                    if value is not None and isinstance(value, datetime):
                        change[key] = int(value.replace(
                            tzinfo = timezone.utc
                        ).timestamp() * 1000)

    return updated_record

#   GET   /analytics/employees/{emp_code}/monthly
@app.get("/analytics/employees/{emp_code}/monthly", status_code= 200)
def employee_monthly(emp_code : str, month : str):

    try :
        month_date = datetime.strptime(month, "%Y-%m").date()

        if month_date.strftime('%Y-%m') != month:
            raise ValueError()

    except ValueError:
        raise HTTPException(422, "Month must be in YYYY-MM format")

    employee = db.employees.find_one({'emp_code' : emp_code})

    if employee is None :
        raise HTTPException(404 , "Employee not found")

    start_date = month + "-01"

    if month_date.month == 12:
        end_date = str(month_date.year + 1) + "-01-01"
    else:
        end_date = str(month_date.year) + "-" + str(month_date.month + 1).zfill(2) + "-01"

    pipeline = [{
        "$match" : {
            "emp_code" : emp_code,
            "date" : {
                '$gte' : start_date,
                "$lt" : end_date
            }
        }
    },{
        "$group" : {
            "_id" : None,


            "present_days" : {
                "$sum" : {
                    "$cond" : [
                        {
                            "$and" : [
                                {"$in" : [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]},
                                {"$gte" : [
                                    {"$dayOfWeek" : {
                                        "$dateFromString" : {
                                            "dateString" : "$date",
                                            "format" : "%Y-%m-%d"
                                        }
                                    }},
                                    2
                                ]},
                                {"$lte" : [
                                    {"$dayOfWeek" : {
                                        "$dateFromString" : {
                                            "dateString" : "$date",
                                            "format" : "%Y-%m-%d"
                                        }
                                    }},
                                    6
                                ]}
                            ]
                        },
                        {
                            "$cond" : [
                                {"$eq" : ["$half_day", True]},
                                0.5,
                                1
                            ]
                        },
                        0
                    ]
                }
            },


            "leave_days" : {
                '$sum' : {
                    '$cond' : [
                        {'$eq' : ['$status', 'LEAVE']},
                        1,0
                    ]
                }
            },


            "late_count" : {
                "$sum" : {
                    '$cond' : [
                        {"$gt" : [
                            {'$ifNull' : ['$late_minutes', 0]},
                            0
                        ]},
                        1,0
                    ]
                }
            },
            

            "total_late_minutes" : {
                "$sum" : {
                    "$ifNull" : ["$late_minutes", 0]
                }
            },


            'total_overtime_minutes' : {
                '$sum' : {
                    '$ifNull' : ['$overtime_minutes', 0]
                }
            }


        }
    }]

    joined_date = datetime.strptime(employee['joined_on'], "%Y-%m-%d").date()

    first_date = max(month_date, joined_date)
    last_date = datetime.strptime(end_date, "%Y-%m-%d").date()

    working_days = 0
    current_date = first_date

    while current_date < last_date:
        if current_date.weekday() < 5:
            working_days += 1

        current_date += timedelta(days = 1)

    result = list(db.attendance_logs.aggregate(pipeline))

    if result:
        summary = result[0]
    else :
        summary = {
            "present_days" : 0,
            "leave_days" : 0,
            "late_count" : 0,
            "total_late_minutes" : 0,
            "total_overtime_minutes" : 0
        }

    present_days = summary['present_days']

    if working_days == 0 :
        attendance_pct = None
    else :
        attendance_pct = round(present_days / working_days * 100, 2)

    return {
        "emp_code" : emp_code,
        "month" : month,
        "working_days" : working_days,
        "present_days" : present_days,
        "leave_days" : summary['leave_days'],
        "late_count" : summary['late_count'],
        'total_late_minutes' : summary['total_late_minutes'],
        "total_overtime_minutes" : summary['total_overtime_minutes'],
        "attendance_pct" : attendance_pct
    }

#   GET   /analytics/departments/summary
@app.get("/analytics/departments/summary", status_code= 200)
def department_summary(month : str, department : str = None):

    try :
        month_date = datetime.strptime(month, "%Y-%m").date()

        if month_date.strftime('%Y-%m') != month:
            raise ValueError()

    except ValueError:
        raise HTTPException(422, "Month must be in YYYY-MM format")

    start_date = month + '-01'

    if month_date.month == 12:
        end_date = str(month_date.year + 1) + "-01-01"
    else:
        end_date = str(month_date.year) + "-" + str(month_date.month + 1).zfill(2) + "-01"

    last_date = (datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days = 1)).isoformat()

    employee_match = {
        "joined_on" : {"$lte" : last_date}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [{
        "$match" : employee_match
    },



    {
        "$lookup" : {
            "from" : "attendance_logs",
            "let" : {
                "employee_code" : '$emp_code'
            },
            "pipeline" : [{
                "$match" : {
                    "$expr" : {
                        "$and" : [
                            {"$eq" : ["$emp_code", "$$employee_code"]},
                            {"$gte" : ["$date", start_date]},
                            {"$lt" : ["$date", end_date]}
                        ]
                    }
                }
            }],
            "as" : "attendance_records"
        }
    },



    {
        "$unwind" : {
            "path" : '$attendance_records',
            "preserveNullAndEmptyArrays" : True
        }
    },



    {
        "$group" : {
            "_id" : {
                "department" : "$department",
                "emp_code" : "$emp_code"
            },



            "present_days" : {
                "$sum" : {
                    "$cond" : [
                        {
                            "$and" : [
                                {"$in" : ["$attendance_records.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$in" : [
                                    {"$dayOfWeek" : {"$dateFromString" : {"dateString" : "$attendance_records.date"}}},
                                    [2, 3, 4, 5, 6]
                                ]}
                            ]
                        },
                        {
                            "$cond" : [
                                {"$eq" : ["$attendance_records.half_day", True]},
                                0.5,
                                1
                            ]
                        },
                        0
                    ]
                }
            },



            "late_count" : {
                "$sum" : {
                    "$cond" : [
                        {"$gt" : ["$attendance_records.late_minutes", 0]},
                        1,
                        0
                    ]
                }
            },



            "total_late_minutes" : {
                "$sum" : {"$ifNull" : ["$attendance_records.late_minutes", 0]}
            },



            "leave_count" : {
                "$sum" : {
                    "$cond" : [
                        {"$eq" : ["$attendance_records.status", "LEAVE"]},
                        1,
                        0
                    ]
                }
            },



            "on_duty_count" : {
                "$sum" : {
                    "$cond" : [
                        {"$eq" : ["$attendance_records.status", "ON_DUTY"]},
                        1,
                        0
                    ]
                }
            },



            "work_hours_sum" : {
                "$sum" : {
                    "$cond" : [
                        {
                            "$and" : [
                                {"$in" : ["$attendance_records.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne" : ["$attendance_records.work_hours", None]}
                            ]
                        },
                        "$attendance_records.work_hours",
                        0
                    ]
                }
            },



            "work_hours_count" : {
                "$sum" : {
                    "$cond" : [
                        {
                            "$and" : [
                                {"$in" : ["$attendance_records.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne" : ["$attendance_records.work_hours", None]}
                            ]
                        },
                        1,
                        0
                    ]
                }
            }
        }
    },



    {
        "$group" : {
            "_id" : "$_id.department",
            "headcount" : {"$sum" : 1},
            "present_days" : {"$sum" : "$present_days"},
            "late_count" : {"$sum" : "$late_count"},
            "total_late_minutes" : {"$sum" : "$total_late_minutes"},
            "leave_count" : {"$sum" : "$leave_count"},
            "on_duty_count" : {"$sum" : "$on_duty_count"},
            "work_hours_sum" : {"$sum" : "$work_hours_sum"},
            "work_hours_count" : {"$sum" : "$work_hours_count"}
        }
    },



    {
        "$addFields" : {
            "avg_work_hours" : {
                "$cond" : [
                    {"$gt" : ["$work_hours_count", 0]},
                    {"$round" : [{"$divide" : ["$work_hours_sum", "$work_hours_count"]}, 2]},
                    None
                ]
            }
        }
    },



    {
        '$match' : {
            "headcount" : {'$gt' : 0}
        }
    },



    {
        "$project" : {
            "_id" : 0,
            "department" : "$_id",
            "headcount" : 1,
            "present_days" : 1,
            "avg_work_hours" : 1,
            "late_count" : 1,
            "total_late_minutes" : 1,
            "leave_count" : 1,
            "on_duty_count" : 1
        }
    },



    {
        "$sort" : {
            "department" : 1
        }
    }
    ]


    items = list(db.employees.aggregate(pipeline))

    return {
        "month" : month,
        "items" : items
    }
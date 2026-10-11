# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne engineering assignment.

## Requirements
- Python 3.11+
- MongoDB 6.0+

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set environment variables `MONGO_URI` and `MONGO_DB`. For local development, a `.env` file is supported.

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

From the repository root:

```bash
uvicorn app.main:app --port 8000
```

The application creates its indexes at startup. To load the provided sample data, place `sample_seed.py` and the `sample_data/` folder at the repository root and run `python sample_seed.py`. Warning: the seed script deletes all existing documents in the two target collections before loading the sample records.

## Endpoints
- `GET /health`
- `POST, GET /employees`
- `POST /attendance/punch-in`
- `POST /attendance/punch-out`
- `GET /attendance`
- `PATCH /attendance/{emp_code}/{date}`
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}`


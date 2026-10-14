# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

I create a unique index on `emp_code` in the employees collection to prevent duplicate employee codes. I also create a unique compound index on `emp_code` and `date` in attendance logs so one employee cannot have multiple attendance records for the same date. These indexes help protect the data and make matching records faster.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

Two punch-in requests can arrive at the same time. Both might check for an existing record before either inserts one. The unique compound index prevents both records from being saved. If one insert fails with a duplicate-key error, the API returns `409 Conflict`.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

Punch-out must happen after punch-in and within 24 hours. The API calculates work hours, overtime minutes, and half-day status before updating the attendance record. The update also checks that `punch_out` is still empty to reduce the chance of updating the same record twice.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

I use MongoDB queries and aggregation pipelines to calculate attendance counts and summaries. Employee information is needed for headcount because an employee may have joined the department but have no attendance records. In the department trend endpoint, a Python loop fills missing calendar dates and calculates the attendance rate and seven-day moving average.

5. **One thing you would change** if this had to serve 100x the data.

If the data becomes 100 times bigger, I would first check which queries are slow and improve the indexes where needed. I would optimize the aggregation pipelines and avoid loading too many records into Python. I would also test endpoints with larger dataset to make sure they remain fast.

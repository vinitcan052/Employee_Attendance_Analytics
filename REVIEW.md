## 1. GET endpoints

### 1.1 `GET /health`

  ---------------------------------------------------------------------------------
  What was wrong          How to notice it        How it was fixed
  ----------------------- ----------------------- ---------------------------------
  The endpoint could      Make MongoDB            Added
  report that the app was unavailable and call    `client.admin.command("ping")`.
  healthy without         `GET /health`. It could If `PyMongoError` occurs, return
  checking whether        still return `200 OK`.  `503 Service Unavailable`.
  MongoDB was reachable.                          

  ---------------------------------------------------------------------------------

### 1.2 `GET /employees`

  ----------------------------------------------------------------------------------
  What was wrong          How to notice it        How it was fixed
  ----------------------- ----------------------- ----------------------------------
  Pagination did not skip With `page=2` and       Used
  the correct number of   `page_size=10`, the     `skip = (page - 1) * page_size`.
  employees.              endpoint should skip    
                          the first 10 matching   
                          employees.              

  The total count should  Change the page while   Used `count_documents(q)` with the
  include every employee  using the same          same filter query.
  matching the filters,   department filter.      
  not just the current    `total` should stay the 
  page.                   same.                   

  The results needed to   Call `GET /employees`   Added sorting by `emp_code`
  be sorted by employee   and inspect the order   ascending before pagination.
  code in ascending       of the employee codes.  
  order.                                          

  The API response needed Create an employee and  Added an `EmployeeOut` response
  to match the `Employee` compare the response    model and used
  response schema.        fields with             `response_model=EmployeeOut`.
                          `openapi.yaml`.         

  The `created_at`        Call `GET /employees`   Convert `created_at` to integer
  response needs epoch    and check the type of   epoch milliseconds for the
  milliseconds, not a     `created_at`.           response.
  datetime string.                                
  ----------------------------------------------------------------------------------

### 1.3 `GET /attendance`

  -----------------------------------------------------------------------
  What was wrong          How to notice it        How it was fixed /
                                                  status
  ----------------------- ----------------------- -----------------------
  The endpoint added an   Call `GET /attendance`  Removed the generated
  `id` field from         and compare its fields  `id`. This was tested
  MongoDB's internal      with the contract.      in Swagger.
  `_id`, although the                             
  attendance response                             
  should not expose an                            
  ID.                                             

  The endpoint needs to   Check the returned      The query should sort
  return records in the   records, especially     by `date` descending
  required order: date    when multiple employees and `emp_code`
  descending, then        have attendance on the  ascending. Verify the
  employee code           same date.              current query uses this
  ascending.                                      sort.

  Pagination should       Test different pages    Use MongoDB sorting,
  return only the         and, later, a large     `skip`, and `limit`.
  requested page and      dataset.                Verify the current
  should not load all                             implementation applies
  matching records into                           these in the database.
  Python.                                         

  Invalid date ranges     Send `date_from` later  Still needs to be
  must be rejected.       than `date_to`;         checked and tested.
                          expected result is      
                          `422`.                  
  -----------------------------------------------------------------------

## 2. POST endpoints

### 2.1 `POST /employees` --- create employee

  -----------------------------------------------------------------------
  What was wrong          How to notice it        How it was fixed
  ----------------------- ----------------------- -----------------------
  Employee input fields   Send an invalid         Added Pydantic `Field`
  needed checks for the   employee code, email,   rules for the required
  required format and     name, department, or    patterns and length
  length.                 shift time.             limits.

  Shift start and shift   Try creating an         Added model validation
  end could be equal,     employee with both      to reject equal shift
  although the contract   shift times set to      times.
  does not allow it.      `09:30`.                

  `created_at` must be    Create an employee and  Store a UTC datetime
  stored as a datetime    inspect the stored      and convert the
  but returned as epoch   value and API response  response value to
  milliseconds.           type.                   integer epoch
                                                  milliseconds.

  Checking for an         Send two requests at    Added a unique index on
  existing employee code  the same time with the  `emp_code` and handle
  before inserting is not same employee code.     `DuplicateKeyError`
  enough if two requests                          with `409 Conflict`.
  arrive together.                                
  -----------------------------------------------------------------------

### 2.2 `POST /attendance/punch-in`

  -----------------------------------------------------------------------
  What was wrong          How to notice it        How it was fixed /
                                                  status
  ----------------------- ----------------------- -----------------------
  The status field could  Try sending status      Restricted it to
  accept values outside   `HOLIDAY`.              `PRESENT`, `WFH`, and
  the allowed statuses.                           `ON_DUTY`, with
                                                  `PRESENT` as the
                                                  default.

  Punch-in did not handle Punch in with an        Check for the employee
  an unknown employee     employee code that does first and return `404`
  correctly.              not exist.              if not found.

  Punch-in time must use  Send a timestamp with   Convert to UTC datetime
  whole-second precision. milliseconds and        and remove
                          inspect the stored or   microseconds.
                          returned time.          

  Attendance date must    Test a punch-in near    Convert the timestamp
  use IST and follow the  midnight IST for an     to IST and apply the
  overnight-shift rule.   overnight shift.        overnight-shift date
                                                  rule.

  The 10-minute grace     For a 09:30 shift, test The helper was updated,
  period has a precise    09:40:00 and 09:40:01;  but these exact
  boundary, and overnight expected late minutes   boundary cases should
  shifts need the correct are 0 and 10. Also test be tested against the
  shift-start date.       an overnight shift.     final code before
                                                  claiming the fix is
                                                  complete.

  Checking for an         Send simultaneous       Use a unique compound
  existing attendance     punch-in requests for   index on
  record before inserting the same employee and   `(emp_code, date)` and
  is not safe for         date. Exactly one       handle
  simultaneous requests.  should return `201`;    `DuplicateKeyError`.
                          the others should       Verify the current
                          return `409`.           startup code creates
                                                  this index.
  -----------------------------------------------------------------------

## 3. Things checked and considered correct

-   `page` starts at `1`, so the first page does not skip records.
-   `skip = (page - 1) * page_size` is the correct calculation.
-   `page_size` limits how many records are returned on a page.
-   The total count should use the same filters as the list query, but
    should count all matching records.
-   `created_at` is generated by the server; the client does not need to
    send it.
-   MongoDB's `_id` is an internal database field and should not be
    exposed when the API contract does not include it.
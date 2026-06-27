-- DEPRECATED: job-level CSV export (debug/fallback only).
-- Primary workflow: aggregated payload — see sql/export_aggregated.sql and docs/payload-schema.md
--
-- No query text. Times are Asia/Kolkata. Default window: rolling last 40 days.

WITH jobs AS (
  SELECT
    job_id,
    user_email,
    (
      SELECT value
      FROM UNNEST(labels)
      WHERE key = 'queried_by'
    ) AS queried_by,
    (
      SELECT value
      FROM UNNEST(labels)
      WHERE key = 'requestor'
    ) AS requestor,
    DATE(creation_time, 'Asia/Kolkata') AS date,
    TIME(creation_time, 'Asia/Kolkata') AS time,
    ROUND(total_bytes_processed / POW(1024, 3), 2) AS gb_scanned
  FROM
    `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE
    DATE(creation_time, 'Asia/Kolkata')
      BETWEEN DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 39 DAY)
          AND CURRENT_DATE('Asia/Kolkata')
    AND job_type = 'QUERY'
    AND state = 'DONE'
    AND total_bytes_processed > 0
)

SELECT
  job_id,
  date,
  time,
  user_email,
  queried_by,
  requestor,
  COALESCE(requestor, queried_by, user_email) AS query_requested_by,
  gb_scanned
FROM
  jobs
ORDER BY
  date DESC,
  time DESC

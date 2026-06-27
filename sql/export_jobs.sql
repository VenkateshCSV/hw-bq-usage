-- BigQuery usage export for hw-bq-usage dashboard
-- Run in BigQuery console or via: bq query --use_legacy_sql=false --format=csv < sql/export_jobs.sql > exports/latest.csv
-- Adjust project/region if your INFORMATION_SCHEMA lives elsewhere.

SELECT
  job_id,
  FORMAT_DATE('%Y-%m-%d', DATE(creation_time)) AS date,
  FORMAT_TIME('%H:%M:%S', TIME(creation_time)) AS time,
  user_email,
  query_requested_by,
  ROUND(total_bytes_processed / POW(1024, 3), 4) AS gb_scanned
FROM
  `region-us`.INFORMATION_SCHEMA.JOBS
WHERE
  job_type = 'QUERY'
  AND state = 'DONE'
  AND total_bytes_processed > 0
  AND creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
ORDER BY
  creation_time DESC

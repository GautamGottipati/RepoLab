# Import — `/`

## Purpose

Take a repository URL and start investigating it. The first thing anyone sees,
so it also has to say what RepoLab is in one line without a tour.

## Reads and writes

- `POST /jobs` `{repo_url}` → `202 {job_id, events_url}`, then navigate to
  `/r/{job_id}`.

## Layout

```
┌────────────────────────────────────────────────────┐
│  RepoLab                                           │
│  Turn a repository into an executable experiment.  │
│                                                    │
│  ┌──────────────────────────────┐  ┌────────────┐  │
│  │ https://github.com/owner/name│  │ Investigate│  │
│  └──────────────────────────────┘  └────────────┘  │
│  Try: AlexIoannides/pyspark-example-project        │
│                                                    │
│  What happens next                                 │
│  1 Inspect the repository      3 Run it for real   │
│  2 Understand how it runs      4 Test a question   │
└────────────────────────────────────────────────────┘
```

The demo repository is offered as a one-click example, because the fastest way
to understand RepoLab is to watch it work on something.

## States

- **Idle** — the field, prefilled with the demo repository.
- **Submitting** — the button is disabled and says so.
- **Rejected** — a 400 from URL validation is shown verbatim under the field.
  Validation lives in the backend (`repolab/ingest/url.py`), and the browser
  does not reimplement it.
- **Unreachable API** — says the API is not responding and names the URL it
  tried, since the usual cause is the worker or API not running.

## Must never show

- A fabricated list of "popular repositories" that were never ingested.
- A success state before the job row exists.

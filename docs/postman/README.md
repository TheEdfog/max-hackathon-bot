# Postman smoke tests for RezumIT

These files let you run a visible HTTP smoke/e2e scenario and make screenshots for the diploma.

## Files

- `rezumit_smoke_tests.postman_collection.json` - Postman collection with tests.
- `rezumit.local.postman_environment.json` - local environment with `base_url=http://127.0.0.1:8000`.

## How to run in Postman

1. Start the server:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.web.main:app --host 127.0.0.1 --port 8000
```

2. Open Postman.
3. Import both JSON files from this folder.
4. Select environment `РезюмИТ Local`.
5. Open collection `РезюмИТ Smoke / E2E Tests`.
6. Click `Run`.
7. Run all requests in order.
8. Take screenshots of the runner results.

The collection creates a temporary user, profile, and vacancy in the local database. It does not generate resume/cover-letter documents because that would make the smoke test depend on LLM/network latency.

## What the collection checks

- Health endpoint returns `status=ok`.
- Login page is available.
- Test user can register and receives an auth cookie.
- Dashboard opens for an authenticated user.
- Candidate profile can be created and displayed.
- Vacancy can be created from text.
- Vacancy requirements extraction route redirects to analysis.
- Analysis page opens for the selected vacancy.
- Match analysis page renders score/recommendation blocks.

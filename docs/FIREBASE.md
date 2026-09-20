# Firebase Hosting

This project includes a minimal Firebase Hosting configuration to serve static assets from the Django `telehealth/static` directory.

Files added:
- `firebase.json` — hosting config (public: `telehealth/static`).
- `.firebaserc` — placeholder Firebase project id.

Quick deploy steps:

1. Install the Firebase CLI:

```bash
npm install -g firebase-tools
```

2. Build/collect static files from Django:

```bash
python manage.py collectstatic --noinput
```

3. Log in and select project (or update `.firebaserc`):

```bash
firebase login
firebase use --add
```

4. Deploy hosting:

```bash
firebase deploy --only hosting
```

Notes and options:
- If you want Firebase Hosting to proxy requests to a backend (Cloud Run/Cloud Functions), add `rewrites` to `firebase.json` that target your service.
- If you serve a single-page app, add a rewrite to `index.html`.
- This repo keeps static files under `telehealth/static`. Ensure `STATIC_ROOT` and `collectstatic` are configured before deploy.

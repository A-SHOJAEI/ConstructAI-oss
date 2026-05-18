# ConstructAI — Frequently Asked Questions

## General

### What is ConstructAI?
ConstructAI is an AI-powered construction management platform that combines safety monitoring, document intelligence, schedule optimization, and project controls into a single system.

### What browsers are supported?
Chrome, Edge, Firefox, and Safari (latest two versions). Mobile browsers are supported for read-only access.

### Is there a mobile app?
The web application is responsive and works on mobile devices. A progressive web app (PWA) is on the roadmap.

## Safety

### How does the AI safety detection work?
ConstructAI uses YOLOv8 computer vision models trained on construction-specific datasets to detect PPE violations, zone intrusions, and unsafe behaviors from camera feeds. Detections are processed in near real-time.

### What PPE can the system detect?
Hard hats, safety vests, and the absence of either. The system can also detect workers in restricted zones.

### How accurate is the detection?
Target accuracy is mAP@0.5 > 0.75 for general detection, with person detection AP > 0.85 and PPE detection AP > 0.70.

## Documents

### What file formats are supported?
PDF, IFC (BIM), CSV, DOCX, and common image formats.

### How does document search work?
Documents are chunked, embedded using AI models, and stored in a vector database. Searches use a hybrid approach combining semantic similarity and keyword matching.

### Can I compare document versions?
Yes. Navigate to Documents > Compare, select two documents, and the system shows a structured diff of changes.

## Scheduling

### What schedule formats can be imported?
Primavera P6 (.xer, .pmxml), Microsoft Project (.mpp, .mpx, .mspdi, .xml).

### What is the DCMA 14-point check?
The DCMA (Defense Contract Management Agency) 14-point assessment evaluates schedule quality across metrics like logic density, total float, negative float, and more.

## Authentication

### How do I set up MFA?
Go to Settings > Security and click "Enable MFA". Scan the QR code with an authenticator app (Google Authenticator, Authy, etc.) and enter the verification code.

### Can I use SSO?
Yes. Google and Microsoft SSO are supported. Contact your administrator to enable SSO for your organization.

### I forgot my password
Click "Forgot your password?" on the login page and enter your email address. You'll receive a password reset link.

## API

### Is there a REST API?
Yes. All functionality is available via a RESTful API at `/api/v1/`. API documentation is available at `/api/v1/docs`.

### How do I authenticate API requests?
Use Bearer token authentication. Obtain a token via `POST /api/v1/auth/login` and include it in the `Authorization: Bearer <token>` header.

### Are there rate limits?
Yes. The API is rate-limited to prevent abuse. Default limits are 100 requests per minute per user. Contact support for higher limits.

## Deployment

### What infrastructure is required?
ConstructAI requires PostgreSQL (with TimescaleDB), Redis, and optionally Kafka for event streaming. Kubernetes deployment is supported via Helm charts.

### Is the system OSHA compliant?
ConstructAI includes an OSHA 29 CFR 1926 knowledge base for safety standard lookups and compliance checking. However, the system is a tool to assist compliance — it does not guarantee OSHA compliance on its own.

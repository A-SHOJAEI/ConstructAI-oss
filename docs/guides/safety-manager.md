# Safety Manager Guide

## Overview
ConstructAI uses computer vision and AI to provide real-time safety monitoring
on construction sites.

## Key Features

### Real-Time Monitoring
- PPE compliance detection (hard hat, vest, glasses, gloves)
- Exclusion zone violations with automatic alerts
- Fall hazard detection near edges and openings

### Alert Management
Alerts are prioritized by severity:
- **P1 Critical**: Immediate danger, triggers work stoppage notification
- **P2 High**: Serious violation, requires supervisor response
- **P3 Medium**: Minor violation, logged for daily review
- **P4 Low**: Observation, included in weekly safety report

### Safety Zones
Configure safety zones on the site map:
1. Draw zone boundaries on the site layout
2. Set zone type (exclusion, restricted, PPE-required)
3. Assign cameras to monitor each zone
4. Configure alert thresholds

### Incident Response
When a safety incident occurs:
1. AI captures and classifies the incident
2. Automatic notification to safety team and PM
3. Schedule impact assessment generated
4. Incident report auto-populated
5. OSHA-reportable incidents flagged for compliance

# Administrator Guide

## Overview
Platform administrators manage tenants, users, feature flags,
and system configuration.

## Tenant Management

### Creating a Tenant
1. Navigate to Admin > Tenants
2. Click "Create Tenant"
3. Enter organization name and billing plan
4. Set admin email for the new tenant
5. The system creates the org, initializes configuration, and sends invite

### Billing Plans
| Feature | Startup | Growth | Enterprise |
|---------|---------|--------|------------|
| API Calls/month | 10,000 | 100,000 | Unlimited |
| Storage | 10 GB | 100 GB | Unlimited |
| Camera Streams | 5 | 25 | Unlimited |
| LLM Tokens/month | 1M | 10M | Unlimited |
| Documents | 500 | 5,000 | Unlimited |

## Feature Flags
Control feature rollout per tenant:
1. Navigate to Admin > Feature Flags
2. Create or edit a flag
3. Set rollout percentage (0-100%)
4. Add tenant-specific overrides if needed

## User Management
Assign roles to control access:
- **Platform Admin**: Full system access
- **Owner/Developer**: Full project access, org management
- **General Contractor**: Full project operations
- **Project Manager**: Project-level management
- **Subcontractor**: Filtered access to assigned work
- **Inspector**: Quality and safety inspection access
- **Safety Manager**: Safety monitoring and incident management
- **Read Only**: View-only access to reports and dashboards

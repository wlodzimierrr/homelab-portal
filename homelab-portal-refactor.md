# Homelab Portal Refactor Plan

## Purpose

This document captures an architectural assessment of the current portal app and
turns it into a realistic refactor plan. The goal is not a rewrite. The goal is
to improve the existing repo incrementally so onboarding is easier, ownership is
clearer, coupling is lower, and future feature work is safer.

## Executive Summary

The portal currently behaves like a modular monolith:

- The backend has several good domain/helper modules, but one very large
  application entrypoint still owns too much of the API surface and too much
  orchestration.
- The frontend has a useful adapter pattern, but one large API module and a few
  page-level orchestration files concentrate too much contract and flow logic.
- Several areas already have the right shape and should be preserved rather than
  redesigned.

The safest path is:

1. split transport and route surfaces first
2. extract orchestration into application services and feature hooks
3. only then split the largest workflow-heavy files

This preserves behavior while reducing centrality and making later refactors
safer.

## Current Architecture Map

### Backend

The backend currently follows this practical structure:

- `backend/app/main.py`
  - FastAPI app entrypoint
  - auth dependency wiring
  - request/response models
  - route handlers
  - background reconciliation loop startup
  - a large amount of orchestration and integration glue
- domain/helper modules under `backend/app/*.py`
  - deployments: `deployment_records.py`, `deployment_locks.py`,
    `deployment_reconciler.py`
  - observability: `monitoring_providers.py`, `observability_config.py`,
    `logs_quickview.py`, `health_timeline.py`, `service_observability.py`
  - catalog and sync: `service_registry_sync.py`, `gitops_project_sync.py`,
    `catalog_reconciliation.py`, `projects_backfill.py`
  - scaffold and admin flows: `scaffold_service.py`, `config_editing.py`,
    `secret_editing.py`
  - shared integration layer: `lib/git_service.py`

The backend pattern is best described as:

- modular monolith
- thin helper modules plus one oversized application/controller module
- route handlers often performing orchestration directly

### Frontend

The frontend currently follows this practical structure:

- `frontend/src/App.tsx`
  - shell
  - manual routing
  - auth redirect behavior
  - theme persistence
  - incident polling
- `frontend/src/pages/*`
  - page-level orchestration
  - local filters, selection state, and async loading
- `frontend/src/lib/adapters/*`
  - backend-to-UI normalization layer
  - view-model shaping for pages
- `frontend/src/lib/api.ts`
  - HTTP client
  - API error handling
  - auth/session side effects
  - endpoint functions
  - backend/frontend contracts

The frontend pattern is best described as:

- lightweight SPA shell
- manual route selection
- page orchestration + adapter layer
- one oversized transport/contracts module

## What Is Already Reasonable

These parts are already pretty healthy and should mostly stay as-is:

### Small frontend utilities

- `frontend/src/lib/auth.ts`
- `frontend/src/lib/service-identity.ts`
- `frontend/src/lib/incident-alerts.ts`
- `frontend/src/lib/deployment-alerts.ts`
- `frontend/src/lib/uptime-status.ts`

Why they are reasonable:

- small
- focused
- easy to test
- low coupling

### Cohesive backend helper modules

- `backend/app/lib/git_service.py`
- `backend/app/release_traceability.py`
- `backend/app/catalog_reconciliation.py`
- `backend/app/health_timeline.py`
- `backend/app/service_observability.py`
- `backend/app/observability_config.py`
- `backend/app/alerts_feed.py`

Why they are reasonable:

- domain-focused
- mostly pure or integration-bounded
- already test-friendly
- relatively easy to reason about in isolation

### Existing architectural direction worth preserving

These patterns should be preserved and expanded:

- frontend adapter layer between raw API responses and page models
- backend helper modules for domain logic
- explicit allowlists and policy modules for sensitive flows
- strong use of backend tests around pure/helper modules

## Structural Smells

## 1. `backend/app/main.py` is too central

File:

- `backend/app/main.py`

Problems:

- owns FastAPI app setup, auth, models, routes, caches, startup hooks, metrics,
  orchestration, and integration glue
- route handlers are not consistently thin
- read endpoints sometimes trigger reconciliation/freshness work
- hard to test without importing the entire application surface
- hard to split ownership across auth, deployments, observability, scaffold, and
  catalog concerns

Why this matters:

- onboarding requires learning too many concepts in one file
- feature work keeps increasing coupling because new endpoints naturally land
  there
- route-level regressions become hard to isolate

## 2. `frontend/src/lib/api.ts` is too broad

File:

- `frontend/src/lib/api.ts`

Problems:

- mixes HTTP transport and error handling with domain contracts
- owns unauthorized redirect signaling
- contains compatibility rollout logic
- contains auth payloads, catalog contracts, deployments contracts,
  observability contracts, scaffold contracts, and admin mutation contracts
- encourages every new feature to depend on one central file

Why this matters:

- backend/frontend contract drift is harder to contain
- simple API changes create unrelated merge conflicts
- adapters are forced to depend on a catch-all module

## 3. Several frontend pages mix rendering and orchestration

Files:

- `frontend/src/pages/service-details-page.tsx`
- `frontend/src/pages/service-deployments-page.tsx`
- `frontend/src/pages/services-page.tsx`
- `frontend/src/pages/dashboard-page.tsx`
- `frontend/src/pages/platform-health-page.tsx`

Problems:

- rendering, loader orchestration, fallback behavior, selection state, and admin
  actions live together
- difficult to unit test page logic without rendering the whole page
- future feature additions will keep increasing page complexity

Why this matters:

- page files become the frontend equivalent of mini `main.py` files
- ownership becomes page-based rather than feature-based

## 4. High-risk backend workflows are monolithic

Files:

- `backend/app/scaffold_service.py`
- `backend/app/deployment_reconciler.py`
- `backend/app/secret_editing.py`
- `backend/app/config_editing.py`

Problems:

- validation, policy, manifest transformation, runtime integration, and workflow
  orchestration are mixed together
- behavior is practical, but the modules are harder to extend safely
- good seams exist conceptually, but they are not formalized in code

Why this matters:

- scaffold flows and deploy/reconcile flows are likely future growth areas
- these are sensitive flows where regressions are costly

## 5. Route layer and background jobs are not clearly separated

Files:

- `backend/app/main.py`
- `backend/app/deployment_reconciler.py`
- `backend/app/service_registry_sync.py`
- `backend/app/gitops_project_sync.py`

Problems:

- startup/background loop lifecycle is coupled to the main HTTP module
- some refresh/reconcile behavior is tied to request paths
- background orchestration is not obviously shared through an application service
  layer

Why this matters:

- hard to test startup/job behavior independently
- refactoring route handlers risks changing background behavior accidentally

## 6. Contract ownership is spread awkwardly

Current state:

- backend response models are concentrated in `main.py`
- frontend request/response interfaces are concentrated in `api.ts`

Problems:

- there is no clear domain-based contract ownership
- auth, catalog, observability, and scaffold contracts are not grouped together
  by feature
- routes and contracts are tightly co-located in central files rather than in
  domain modules

## Target Structure

The target architecture should still be a modular monolith, but with clearer
layers and domain ownership.

### Backend target shape

Dependency direction:

- `api/routes/*` -> `services/*` -> `domain helpers / integrations`

More concretely:

- route modules own HTTP concerns only
- service modules own orchestration/use cases
- helper modules own parsing, normalization, persistence, mutation, and provider
  interaction
- startup wiring owns process lifecycle, not business logic

Suggested backend structure:

```text
backend/app/
  api/
    app.py
    deps/
      auth.py
    routes/
      system.py
      auth.py
      catalog.py
      deployments.py
      observability.py
      scaffold.py
      admin_mutations.py
    schemas/
      auth.py
      catalog.py
      deployments.py
      observability.py
      scaffold.py
  services/
    catalog_service.py
    deployment_service.py
    observability_service.py
    scaffold_admin_service.py
    startup_jobs.py
  deployments/
    reconciler/
      github_events.py
      live_state.py
      comments.py
      reconcile.py
  scaffold/
    types.py
    validation.py
    catalog.py
    render.py
    templates/
      python.py
      node.py
      frontend.py
      database.py
      wordpress.py
```

### Frontend target shape

Dependency direction:

- `pages / components` -> `feature hooks / adapters` -> `api domain modules` ->
  `http client`

Suggested frontend structure:

```text
frontend/src/
  app/
    AppShell.tsx
    router.ts
    use-theme.ts
    use-incident-feed.ts
  lib/
    http/
      client.ts
      errors.ts
      auth-events.ts
    api/
      auth.ts
      catalog.ts
      deployments.ts
      observability.ts
      scaffold.ts
      admin.ts
      contracts/
        auth.ts
        catalog.ts
        deployments.ts
        observability.ts
        scaffold.ts
        admin.ts
  features/
    service-details/
      page.tsx
      use-service-overview.ts
      use-service-actions.ts
      use-service-observability.ts
      components/
    service-deployments/
      page.tsx
      use-deployment-history.ts
      use-deployment-observability.ts
      components/
```

## Exact File Splits For Highest-Risk Files

### Split `backend/app/main.py`

Current role:

- FastAPI entrypoint
- auth dependency logic
- schemas
- routes
- caches
- startup jobs
- orchestration helpers

Split into:

- `backend/app/api/app.py`
  - FastAPI app construction
  - router registration
  - middleware
  - startup/shutdown wiring
- `backend/app/api/deps/auth.py`
  - bearer auth
  - current user resolution
  - admin checks
- `backend/app/api/routes/system.py`
  - `/health`
  - `/metrics`
- `backend/app/api/routes/auth.py`
  - `/auth/login`
- `backend/app/api/routes/catalog.py`
  - `/projects`
  - `/projects/diagnostics`
  - `/services`
  - `/services/{service_id}`
  - `/catalog/reconciliation`
  - service registry diagnostics
- `backend/app/api/routes/deployments.py`
  - deployment history/info
  - deploy/promote/rollback/config-change endpoints
  - deployment cancel and reconcile endpoints
- `backend/app/api/routes/observability.py`
  - service metrics
  - service trends
  - deployment observability
  - alerts/provider diagnostics
- `backend/app/api/routes/scaffold.py`
  - scaffold preview/submit
- `backend/app/api/routes/admin_mutations.py`
  - public hostname
  - config edit
  - secret edit
- `backend/app/api/schemas/*.py`
  - move Pydantic models out of `main.py`

### Split `frontend/src/lib/api.ts`

Current role:

- raw request client
- errors
- auth/session side effects
- response/request contracts
- endpoint functions

Split into:

- `frontend/src/lib/http/client.ts`
  - `request`
  - low-level fetch behavior
  - base URL joining
  - auth header injection
- `frontend/src/lib/http/errors.ts`
  - `ApiRequestError`
  - `ApiAuthDiagnosticError`
  - helper type guards
- `frontend/src/lib/http/auth-events.ts`
  - unauthorized event constant
  - event emission utilities
- `frontend/src/lib/api/auth.ts`
  - login
  - auth-specific request/response types
- `frontend/src/lib/api/catalog.ts`
  - projects
  - services
  - reconciliation
  - diagnostics
- `frontend/src/lib/api/deployments.ts`
  - deployment info/history
  - deploy/promote/rollback
  - config/secret edits if they remain under deployment/admin scope
- `frontend/src/lib/api/observability.ts`
  - provider diagnostics
  - metrics
  - trends
  - deployment observability
- `frontend/src/lib/api/scaffold.ts`
  - preview
  - submit
- `frontend/src/lib/api/contracts/*.ts`
  - shared domain contracts grouped by feature

### Split `frontend/src/pages/service-details-page.tsx`

Current role:

- overview rendering
- data fan-in
- observability data
- admin actions
- deploy actions
- config/secret/public-host mutation flows

Split into:

- `frontend/src/features/service-details/page.tsx`
  - page composition only
- `frontend/src/features/service-details/use-service-overview.ts`
  - service detail loading
  - related release/runtime loading
- `frontend/src/features/service-details/use-service-actions.ts`
  - deploy/promote/rollback/config/public-host mutations
- `frontend/src/features/service-details/use-service-observability.ts`
  - summary/trend/load state for service metrics panels
- `frontend/src/features/service-details/components/`
  - `overview-card.tsx`
  - `deployment-actions-panel.tsx`
  - `config-editor-panel.tsx`
  - `secret-editor-panel.tsx`
  - `service-observability-panel.tsx`

### Split `frontend/src/pages/service-deployments-page.tsx`

Split into:

- `frontend/src/features/service-deployments/page.tsx`
- `frontend/src/features/service-deployments/use-deployment-history.ts`
- `frontend/src/features/service-deployments/use-deployment-observability.ts`
- `frontend/src/features/service-deployments/components/deployment-list.tsx`
- `frontend/src/features/service-deployments/components/deployment-observability-panel.tsx`
- `frontend/src/features/service-deployments/components/impact-badges.tsx`

### Split `backend/app/scaffold_service.py`

Split into:

- `backend/app/scaffold/types.py`
  - scaffold request/response input structures
- `backend/app/scaffold/validation.py`
  - service name validation
  - template validation
  - field normalization
- `backend/app/scaffold/catalog.py`
  - catalog entry generation
  - AppProject updates
- `backend/app/scaffold/render.py`
  - file generation orchestration
- `backend/app/scaffold/templates/python.py`
- `backend/app/scaffold/templates/node.py`
- `backend/app/scaffold/templates/frontend.py`
- `backend/app/scaffold/templates/database.py`
- `backend/app/scaffold/templates/wordpress.py`

### Split `backend/app/deployment_reconciler.py`

Split into:

- `backend/app/deployments/reconciler/github_events.py`
  - PR scanning
  - title/head parsing
  - event extraction
- `backend/app/deployments/reconciler/live_state.py`
  - live rollout verification
  - Argo/image comparison helpers
- `backend/app/deployments/reconciler/comments.py`
  - PR comment body building
  - comment poster helper
- `backend/app/deployments/reconciler/reconcile.py`
  - reconciliation loop
  - upsert orchestration
  - lock sync integration

### Split `backend/app/config_editing.py` and `backend/app/secret_editing.py`

Config editing split:

- `backend/app/admin/config_targets.py`
  - allowlisted config edit targets
  - editable keys
- `backend/app/admin/config_manifests.py`
  - config map parsing/updating
  - checksum generation
  - deployment patch update
- `backend/app/admin/rate_limit.py`
  - shared in-memory rate limit helpers

Secret editing split:

- `backend/app/admin/secret_targets.py`
  - allowlisted secret edit targets
- `backend/app/admin/sops_runtime.py`
  - SOPS runtime validation
  - config path handling
  - decrypt/encrypt shell integration
- `backend/app/admin/secret_manifests.py`
  - secret manifest mutation
  - encoding mode logic
- `backend/app/admin/rate_limit.py`
  - shared rate limiting

## Phased Migration Plan

## Phase 0: Lock Down Behavior

Goal:

- make refactoring safe before moving code

Work:

- expand backend API smoke coverage by domain
- add frontend tests for API/adapters that currently have no coverage
- document route groups and contract groups

Success criteria:

- route-level smoke tests exist for auth, catalog, deployments, observability,
  scaffold, config edit, and secret edit
- frontend adapters have fixture-based tests for the major domains

## Phase 1: Split Transport Surfaces

Goal:

- reduce centrality without changing runtime behavior

Work:

- split `frontend/src/lib/api.ts` by domain
- split backend schemas and route groups out of `main.py`
- keep existing helpers and business logic in place for now

Success criteria:

- the app behaves the same
- imports become domain-based instead of catch-all
- merge conflicts in `api.ts` and `main.py` drop materially

## Phase 2: Extract Application Services

Goal:

- separate HTTP concerns from orchestration concerns

Work:

- introduce backend service modules for catalog, deployments, observability,
  scaffold/admin flows
- route modules call services
- startup/job wiring uses the same services

Success criteria:

- route handlers are thin
- orchestration can be tested without spinning up FastAPI
- shared use cases are no longer duplicated across routes and jobs

## Phase 3: Split Feature-Heavy Frontend Pages

Goal:

- turn page files into composition layers

Work:

- extract hooks and feature components from service details and deployments pages
- move loader and action logic out of page bodies

Success criteria:

- page files become readable top-level compositions
- async logic can be tested independently from rendering

## Phase 4: Split High-Risk Workflow Modules

Goal:

- make future feature work in scaffold/reconcile/admin flows safer

Work:

- split scaffold generation by template group and function
- split deployment reconciler by event parsing/live verification/orchestration
- split config/secret editing by policy/runtime/manifest mutation

Success criteria:

- workflow-heavy modules have explicit ownership seams
- sensitive mutations are easier to review and test

## Phase 5: Normalize Contracts and Integration Boundaries

Goal:

- make backend/frontend contracts and operational ownership clearer

Work:

- group request/response schemas by domain
- add contract fixture tests
- optionally evaluate generated OpenAPI clients later

Success criteria:

- contract ownership is domain-based
- the API layer stops being a dumping ground for unrelated types

## Backend Stabilization Bridge

Goal:

- harden the new backend seams before doing another round of structural splits

Work:

- make service dependency wiring test-friendly so monkeypatching and overrides
  still work after service extraction
- add route-level integration coverage for the new route and service boundaries
- remove more non-domain glue from `backend/app/main.py` without changing the
  public app entrypoint

Success criteria:

- service dependency binding can be overridden cleanly in tests
- backend route/service integration checks cover the new layering
- `main.py` is more clearly limited to entrypoint and composition concerns

## Tickets

The tickets below are written as realistic incremental work items. They are
ordered roughly by safety and leverage.

### T7.1.1: Add backend route smoke coverage by domain

Scope:

- split `backend/tests/test_api.py` into:
  - `test_api_auth.py`
  - `test_api_catalog.py`
  - `test_api_deployments.py`
  - `test_api_observability.py`
  - `test_api_scaffold.py`
  - `test_api_admin_mutations.py`

Outcome:

- refactors to route modules can proceed with confidence

### T7.1.2: Add frontend adapter coverage for high-risk domains

Scope:

- add tests for:
  - `frontend/src/lib/adapters/services.ts`
  - `frontend/src/lib/adapters/deployment-observability.ts`
  - `frontend/src/lib/adapters/release-dashboard.ts`
  - `frontend/src/lib/adapters/platform-health.ts`

Outcome:

- adapter refactors and API contract splits become safer

### T7.2.1: Split frontend HTTP client from domain API surface

Scope:

- create:
  - `frontend/src/lib/http/client.ts`
  - `frontend/src/lib/http/errors.ts`
  - `frontend/src/lib/http/auth-events.ts`
- move transport/error/session concerns out of `frontend/src/lib/api.ts`

Outcome:

- lower coupling in the frontend API layer

### T7.2.2: Split frontend API modules by domain

Scope:

- create:
  - `frontend/src/lib/api/auth.ts`
  - `frontend/src/lib/api/catalog.ts`
  - `frontend/src/lib/api/deployments.ts`
  - `frontend/src/lib/api/observability.ts`
  - `frontend/src/lib/api/scaffold.ts`
  - `frontend/src/lib/api/admin.ts`
  - `frontend/src/lib/api/contracts/*`

Outcome:

- contracts and API functions are owned by feature/domain instead of one file

### T7.2.3: Replace direct `api.ts` imports in adapters

Scope:

- update adapters/pages to import only the domain module they need

Outcome:

- cleaner dependency graph

### T7.3.1: Extract backend schemas from `main.py`

Scope:

- create `backend/app/api/schemas/{auth,catalog,deployments,observability,scaffold}.py`
- move Pydantic models out of `backend/app/main.py`

Outcome:

- `main.py` loses one major responsibility without behavior change

### T7.3.2: Split backend routes out of `main.py`

Scope:

- create:
  - `backend/app/api/app.py`
  - `backend/app/api/deps/auth.py`
  - `backend/app/api/routes/system.py`
  - `backend/app/api/routes/auth.py`
  - `backend/app/api/routes/catalog.py`
  - `backend/app/api/routes/deployments.py`
  - `backend/app/api/routes/observability.py`
  - `backend/app/api/routes/scaffold.py`
  - `backend/app/api/routes/admin_mutations.py`

Outcome:

- route concentration drops sharply

### T7.3.3: Keep startup/job wiring explicit but separate

Scope:

- create `backend/app/services/startup_jobs.py`
- move reconciler thread startup/shutdown wiring there

Outcome:

- route app construction and job lifecycle stop living together

### T7.4.1: Introduce backend deployment service layer

Scope:

- create `backend/app/services/deployment_service.py`
- move deployment orchestration out of route handlers

Belongs there:

- load deployment info/history
- deploy/promote/rollback orchestration
- lock handling coordination
- snapshot storage coordination

### T7.4.2: Introduce backend observability service layer

Scope:

- create `backend/app/services/observability_service.py`

Belongs there:

- deployment observability orchestration
- metrics/timeline/log composite assembly
- provider failure translation
- request window resolution

### T7.4.3: Introduce backend catalog service layer

Scope:

- create `backend/app/services/catalog_service.py`

Belongs there:

- project/service listing orchestration
- catalog reconciliation fetch and diagnostics
- service identity diagnostics orchestration
- read-through freshness/reconcile behavior if retained

### T7.4.4: Introduce backend scaffold/admin service layer

Scope:

- create `backend/app/services/scaffold_admin_service.py`

Belongs there:

- scaffold preview/submit orchestration
- public hostname changes
- config edit orchestration
- secret edit orchestration

### T7.5.1: Split service details frontend feature

Scope:

- create:
  - `frontend/src/features/service-details/page.tsx`
  - `use-service-overview.ts`
  - `use-service-actions.ts`
  - `use-service-observability.ts`
  - feature subcomponents

Outcome:

- the highest-complexity frontend page becomes maintainable

### T7.5.2: Split service deployments frontend feature

Scope:

- create:
  - `frontend/src/features/service-deployments/page.tsx`
  - `use-deployment-history.ts`
  - `use-deployment-observability.ts`
  - feature subcomponents

Outcome:

- deployment drill-down behavior becomes easier to evolve

### T7.5.3: Slim `frontend/src/App.tsx`

Scope:

- create:
  - `frontend/src/app/AppShell.tsx`
  - `frontend/src/app/router.ts`
  - `frontend/src/app/use-theme.ts`
  - `frontend/src/app/use-incident-feed.ts`

Outcome:

- shell concerns stop accumulating in one file

### T7.6.1: Split scaffold generation by template group

Scope:

- create scaffold package under `backend/app/scaffold/`

Outcome:

- template work stops expanding a monolithic file

### T7.6.2: Split deployment reconciler into event parsing and orchestration

Scope:

- create `backend/app/deployments/reconciler/`

Outcome:

- GitHub parsing, live verification, and DB coordination become separately testable

### T7.6.3: Split config and secret editing into policy/runtime/mutation modules

Scope:

- create `backend/app/admin/` modules described above

Outcome:

- admin mutation flows become easier to reason about and review safely

### T7.6.4: Stabilize backend service dependency wiring

Scope:

- refactor backend service construction so test overrides do not depend on
  import-time binding in `main.py`
- make service dependency wiring explicit and swappable at composition time
- preserve the existing `app.main:app` entrypoint

Belongs there:

- service dependency builders
- override-friendly composition helpers
- test-safe wiring for `catalog_service`, `deployment_service`,
  `observability_service`, and `scaffold_admin_service`

Outcome:

- service-layer refactors stop breaking monkeypatch-heavy tests and route-level
  integration coverage becomes easier to maintain

### T7.6.5: Add backend integration coverage for new route and service boundaries

Scope:

- add or expand backend tests to verify route modules, service wiring, and
  route-to-service integration behavior after the recent backend splits
- specifically cover auth, catalog, deployments, observability, scaffold, and
  admin mutation flows through the new route modules

Belongs there:

- route-level integration checks for `backend/app/api/routes/*`
- service-wiring checks for the extracted backend services
- regression coverage for request-time reconciliation and admin mutation flows

Outcome:

- the new route and service layering is protected before more backend cleanup

### T7.6.6: Extract remaining backend composition glue from `main.py`

Scope:

- move non-domain app composition logic out of `backend/app/main.py` while
  keeping `app.main:app` stable

Belongs there:

- app/bootstrap creation helpers
- backend service dependency builders
- environment/config/runtime helper cluster
- composition-only helpers that do not belong to auth, catalog, deployments,
  observability, or scaffold domains

Outcome:

- `main.py` becomes primarily an application entrypoint and composition module,
  not a mixed home for runtime wiring and domain-adjacent glue

## Required Tests And Checks Before High-Risk Refactors

### Before splitting `backend/app/main.py`

Need:

- expanded backend route smoke tests
- endpoint-level assertions for auth, deployments, observability, scaffold,
  config edit, secret edit

### Before splitting `frontend/src/lib/api.ts`

Need:

- transport-level tests for unauthorized behavior
- adapter tests covering compatibility fallback behavior
- contract fixture tests for domain modules

### Before splitting service pages

Need:

- adapter fixture tests
- interaction tests for selection/filter behavior where practical

### Before splitting workflow-heavy backend modules

Need:

- scaffold golden/file-output tests
- deployment reconciler event fixture tests
- config/secret mutation manifest tests
- startup/job loop smoke checks if lifecycle wiring moves

## Recommended First Three Tickets

If work needs to start immediately, do these first:

1. `T7.1.1` Add backend route smoke coverage by domain
2. `T7.2.1` Split frontend HTTP client from domain API surface
3. `T7.3.1` Extract backend schemas from `main.py`

Why these three:

- they reduce risk before larger moves
- they create the structure needed for subsequent splits
- they improve onboarding quickly without forcing a large behavior change

## Non-Goals

These are intentionally not part of the current plan:

- rewriting the app around a new framework
- replacing FastAPI or React
- introducing a microservice split
- redesigning the deployment model
- replacing manual routing immediately

The current repo is workable. The problem is not that it needs a new
architecture. The problem is that the current architecture needs to be made
explicit and allowed to breathe.

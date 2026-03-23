"""Role prompts — 43 specialized worker roles with domain expertise.

Each role gets a tailored system prompt with domain description and
Definition of Done (DoD) checklist. This ensures each worker agent
has deep expertise in its specific area.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Role(str, Enum):
    # Discovery & Planning
    PRODUCT_MANAGER = "product_manager"
    BUSINESS_ANALYST = "business_analyst"
    PROJECT_MANAGER = "project_manager"

    # Architecture & Design
    SOLUTION_ARCHITECT = "solution_architect"
    UI_UX_DESIGNER = "ui_ux_designer"
    DATABASE_ARCHITECT = "database_architect"

    # Frontend Development
    UI_COMPONENT_DEV = "ui_component_dev"
    FRONTEND_LOGIC_DEV = "frontend_logic_dev"
    RESPONSIVE_MOBILE_SPECIALIST = "responsive_mobile_specialist"

    # Backend Development
    API_DEV = "api_dev"
    DATABASE_DEV = "database_dev"
    AUTH_SECURITY_DEV = "auth_security_dev"
    BUSINESS_LOGIC_DEV = "business_logic_dev"

    # Infrastructure
    DEVOPS_ENGINEER = "devops_engineer"
    CLOUD_ARCHITECT = "cloud_architect"
    INTEGRATION_DEV = "integration_dev"

    # Quality Assurance
    QA_UNIT_TESTER = "qa_unit_tester"
    QA_INTEGRATION_TESTER = "qa_integration_tester"
    QA_E2E_TESTER = "qa_e2e_tester"
    SECURITY_TESTER = "security_tester"
    PERFORMANCE_TESTER = "performance_tester"

    # Review & Deployment
    SENIOR_CODE_REVIEWER = "senior_code_reviewer"
    DEPLOYMENT_ENGINEER = "deployment_engineer"

    # Agency Expansion Roles
    GROWTH_HACKER = "growth_hacker"
    CONTENT_CREATOR = "content_creator"
    SEO_SPECIALIST = "seo_specialist"
    SOCIAL_MEDIA_STRATEGIST = "social_media_strategist"
    SALES_STRATEGIST = "sales_strategist"
    SALES_ENGINEER = "sales_engineer"
    UI_DESIGNER = "ui_designer"
    UX_RESEARCHER = "ux_researcher"
    BRAND_GUARDIAN = "brand_guardian"
    SUPPORT_RESPONDER = "support_responder"
    ANALYTICS_REPORTER = "analytics_reporter"
    PPC_STRATEGIST = "ppc_strategist"
    AD_CREATIVE_STRATEGIST = "ad_creative_strategist"
    GAME_DESIGNER = "game_designer"
    NARRATIVE_DESIGNER = "narrative_designer"
    TECHNICAL_WRITER = "technical_writer"
    MCP_BUILDER = "mcp_builder"
    DOCUMENT_GENERATOR = "document_generator"
    MOBILE_APP_BUILDER = "mobile_app_builder"
    FULL_STACK_DEV = "full_stack_dev"


# Role definitions: (domain_description, definition_of_done_items)
_ROLE_DEFINITIONS: dict[Role, tuple[str, list[str]]] = {
    Role.PRODUCT_MANAGER: (
        "Product strategy, user stories, acceptance criteria, roadmap planning. "
        "Translate business needs into actionable technical requirements.",
        ["Product vision document created", "User stories with acceptance criteria defined",
         "Priority matrix established", "Success metrics identified"],
    ),
    Role.BUSINESS_ANALYST: (
        "Requirements gathering, BRD creation, feasibility assessment, risk analysis. "
        "Bridge between stakeholders and technical team.",
        ["BRD with functional/non-functional requirements", "User journey maps created",
         "Feasibility assessment completed", "Risk register populated"],
    ),
    Role.PROJECT_MANAGER: (
        "Sprint planning, task breakdown, dependency management, timeline estimation. "
        "Ensure delivery on schedule with quality.",
        ["Sprint plan with task dependencies", "Timeline with milestones",
         "Resource allocation defined", "Risk mitigation plan"],
    ),
    Role.SOLUTION_ARCHITECT: (
        "System architecture design, technology selection, API design, database schema, "
        "scalability patterns, security architecture. Define the technical blueprint.",
        ["Architecture document with diagrams", "API specification (OpenAPI/REST)",
         "Database schema designed", "Technology decisions documented",
         "File ownership map created", "Security architecture defined"],
    ),
    Role.UI_UX_DESIGNER: (
        "User interface design, user experience flows, wireframes, design system, "
        "component library specification, accessibility standards.",
        ["Design system with tokens defined", "Component hierarchy documented",
         "User flows mapped", "Accessibility standards specified"],
    ),
    Role.DATABASE_ARCHITECT: (
        "Database schema design, normalization, indexing strategy, migration planning, "
        "data modeling, query optimization patterns.",
        ["Database schema with relations", "Index strategy defined",
         "Migration scripts planned", "Data validation rules specified"],
    ),
    Role.UI_COMPONENT_DEV: (
        "Build reusable UI components: buttons, forms, cards, modals, navigation, layouts. "
        "Follow the design system. Ensure accessibility and responsive behavior.",
        ["All specified components implemented", "Storybook/demo page created",
         "Accessibility attributes added", "Components follow design tokens"],
    ),
    Role.FRONTEND_LOGIC_DEV: (
        "Frontend state management, API integration, routing, form validation, "
        "error handling, data fetching and caching patterns.",
        ["State management configured", "API integration layer built",
         "Form validation implemented", "Error boundaries in place",
         "Loading/error states handled"],
    ),
    Role.RESPONSIVE_MOBILE_SPECIALIST: (
        "Mobile-first responsive design, touch interactions, viewport optimization, "
        "progressive enhancement, PWA capabilities.",
        ["Mobile breakpoints implemented", "Touch targets properly sized",
         "Viewport meta configured", "PWA manifest if applicable"],
    ),
    Role.API_DEV: (
        "REST/GraphQL API implementation, endpoint design, request validation, "
        "response formatting, pagination, rate limiting, API documentation.",
        ["All API endpoints implemented per spec", "Input validation on all endpoints",
         "Proper HTTP status codes", "API documentation generated",
         "Rate limiting configured", "CORS properly set"],
    ),
    Role.DATABASE_DEV: (
        "Database implementation, migrations, seed data, query optimization, "
        "ORM/query builder setup, connection pooling, backup strategy.",
        ["Database migrations created and tested", "Seed data scripts ready",
         "Indexes created per schema", "Connection pooling configured",
         "Queries optimized for common patterns"],
    ),
    Role.AUTH_SECURITY_DEV: (
        "Authentication (JWT/OAuth/session), authorization (RBAC/ABAC), "
        "password hashing, CSRF protection, input sanitization, security headers.",
        ["Auth system implemented (login/register/logout)",
         "Password hashing with bcrypt/argon2", "JWT/session management",
         "RBAC/permissions system", "Security headers configured",
         "Input sanitization on all endpoints"],
    ),
    Role.BUSINESS_LOGIC_DEV: (
        "Core business rules, domain logic, service layer, workflow engines, "
        "data processing pipelines, third-party integrations.",
        ["Business rules implemented per requirements",
         "Service layer with clean interfaces", "Input validation at service boundary",
         "Error handling with meaningful messages", "Logging for audit trail"],
    ),
    Role.DEVOPS_ENGINEER: (
        "CI/CD pipelines, Docker containerization, infrastructure as code, "
        "monitoring setup, log aggregation, deployment automation.",
        ["Dockerfile(s) created and optimized", "docker-compose for local dev",
         "CI pipeline configured (lint/test/build)", "Environment variable management",
         "Health check endpoints"],
    ),
    Role.CLOUD_ARCHITECT: (
        "Cloud infrastructure design, serverless architecture, CDN configuration, "
        "auto-scaling, cost optimization, multi-region deployment.",
        ["Cloud architecture documented", "Infrastructure as code (Terraform/CDK)",
         "Auto-scaling policies defined", "Cost estimates provided",
         "Deployment runbook created"],
    ),
    Role.INTEGRATION_DEV: (
        "System integration, API gateway, message queues, event-driven architecture, "
        "webhooks, third-party service connectors, data synchronization.",
        ["Integration points connected and tested", "Error handling for external services",
         "Retry/circuit-breaker patterns", "Data transformation layers",
         "Integration tests passing"],
    ),
    Role.QA_UNIT_TESTER: (
        "Unit test creation, code coverage, mock/stub strategies, test data management, "
        "snapshot testing, property-based testing.",
        ["Unit tests for all business logic", "Code coverage ≥80%",
         "Edge cases covered", "Mocks for external dependencies",
         "Test data factories created"],
    ),
    Role.QA_INTEGRATION_TESTER: (
        "Integration testing, API contract testing, database integration tests, "
        "service interaction verification, test environment management.",
        ["Integration tests for all API endpoints", "Database interaction tests",
         "Service-to-service contract tests", "Test environment reproducible"],
    ),
    Role.QA_E2E_TESTER: (
        "End-to-end testing, user flow verification, browser automation, "
        "cross-browser testing, visual regression testing.",
        ["E2E tests for critical user flows", "Cross-browser verification",
         "Form submission flows tested", "Error scenarios covered",
         "Screenshots/snapshots for visual regression"],
    ),
    Role.SECURITY_TESTER: (
        "Security testing, vulnerability assessment, OWASP Top 10 verification, "
        "penetration testing patterns, dependency audit.",
        ["OWASP Top 10 checks completed", "Dependency vulnerability scan",
         "Auth bypass attempts tested", "SQL injection tests",
         "XSS prevention verified", "Security report generated"],
    ),
    Role.PERFORMANCE_TESTER: (
        "Load testing, stress testing, performance benchmarking, memory profiling, "
        "database query performance, API response time optimization.",
        ["Load test scripts created", "Baseline performance metrics established",
         "Bottlenecks identified and documented", "Performance report generated"],
    ),
    Role.SENIOR_CODE_REVIEWER: (
        "Code review for quality, security, performance, maintainability. "
        "Check architecture adherence, naming conventions, error handling, test coverage.",
        ["All files reviewed for security issues", "Architecture adherence verified",
         "Code quality standards met", "Performance anti-patterns flagged",
         "Review comments with actionable feedback"],
    ),
    Role.DEPLOYMENT_ENGINEER: (
        "Deployment execution, environment configuration, DNS setup, SSL certificates, "
        "monitoring verification, rollback readiness.",
        ["Deployment successful to target environment", "SSL/TLS configured",
         "Monitoring dashboards active", "Rollback procedure tested",
         "Deployment runbook completed"],
    ),
    Role.TECHNICAL_WRITER: (
        "API documentation, user guides, README files, architecture decision records, "
        "onboarding documentation, changelog management.",
        ["README with setup instructions", "API documentation complete",
         "Architecture decisions documented", "Contributing guide created"],
    ),
    Role.FULL_STACK_DEV: (
        "End-to-end feature development spanning frontend and backend. "
        "Database, API, UI component, state management, and deployment.",
        ["Feature fully implemented end-to-end", "Frontend and backend connected",
         "Database schema and migrations", "Unit and integration tests",
         "Documentation updated"],
    ),
    Role.MOBILE_APP_BUILDER: (
        "Mobile application development with React Native, Flutter, or native platforms. "
        "Navigation, state management, API integration, app store preparation.",
        ["Mobile app scaffold created", "Navigation configured",
         "API integration layer", "Platform-specific adaptations",
         "App store assets prepared"],
    ),
}

# Roles not in _ROLE_DEFINITIONS get a generic prompt
_GENERIC_DOD = ["Task completed as specified", "Output quality verified", "Documentation updated"]

_PROMPT_TEMPLATE = """\
You are a **{role_name}** working on a software development project.

## Your Domain Expertise
{domain_description}

## Tech Stack
{tech_stack}

## File Ownership Boundaries
{ownership_section}
You MUST only create/modify files within your ownership boundaries.
Do NOT touch files owned by other team members.

## Shared Contracts
{contracts}

## Team Context (Shared Memory)
{shared_memory}

## Definition of Done
Before declaring your work complete, verify ALL of these:
{dod_checklist}

## Rules
1. Stay within your file ownership boundaries
2. Follow the tech stack — do not introduce unauthorized dependencies
3. Reference shared contracts (API spec, DB schema) for interface definitions
4. Write production-quality code with proper error handling
5. Include relevant tests for your work
6. Document your decisions in shared memory for other team members
"""


class RolePromptLibrary:
    """Generates parameterized system prompts for 43 specialized roles."""

    @staticmethod
    def get_prompt(
        role: Role,
        tech_stack: str = "",
        ownership_section: str = "",
        contracts: str = "",
        shared_memory_context: str = "",
    ) -> str:
        defn = _ROLE_DEFINITIONS.get(role)
        if defn:
            domain_desc, dod_items = defn
        else:
            domain_desc = f"Specialist in {role.value.replace('_', ' ')} tasks."
            dod_items = _GENERIC_DOD

        dod_checklist = "\n".join(f"- [ ] {item}" for item in dod_items)

        return _PROMPT_TEMPLATE.format(
            role_name=role.value.replace("_", " ").title(),
            domain_description=domain_desc,
            tech_stack=tech_stack or "Not specified — choose best practices for the project type.",
            ownership_section=ownership_section or "No specific boundaries — coordinate with team.",
            contracts=contracts or "No contracts defined yet.",
            shared_memory=shared_memory_context or "No shared context yet.",
            dod_checklist=dod_checklist,
        )

    @staticmethod
    def list_roles() -> list[str]:
        return [r.value for r in Role]

    @staticmethod
    def roles_for_phase(phase: str) -> list[Role]:
        """Get relevant roles for a pipeline phase."""
        mapping: dict[str, list[Role]] = {
            "discovery": [Role.PRODUCT_MANAGER, Role.BUSINESS_ANALYST],
            "architecture": [Role.SOLUTION_ARCHITECT, Role.UI_UX_DESIGNER, Role.DATABASE_ARCHITECT],
            "sprint_planning": [Role.PROJECT_MANAGER],
            "development": [
                Role.UI_COMPONENT_DEV, Role.FRONTEND_LOGIC_DEV, Role.RESPONSIVE_MOBILE_SPECIALIST,
                Role.API_DEV, Role.DATABASE_DEV, Role.AUTH_SECURITY_DEV,
                Role.BUSINESS_LOGIC_DEV, Role.DEVOPS_ENGINEER, Role.CLOUD_ARCHITECT,
                Role.INTEGRATION_DEV,
            ],
            "qa_security": [
                Role.QA_UNIT_TESTER, Role.QA_INTEGRATION_TESTER, Role.QA_E2E_TESTER,
                Role.SECURITY_TESTER, Role.PERFORMANCE_TESTER,
            ],
            "deployment": [Role.DEPLOYMENT_ENGINEER, Role.DEVOPS_ENGINEER],
            "post_launch": [Role.TECHNICAL_WRITER, Role.SENIOR_CODE_REVIEWER],
        }
        return mapping.get(phase, [])

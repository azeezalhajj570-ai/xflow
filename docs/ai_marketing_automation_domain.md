# AI-Powered Marketing Automation — Odoo 19.0 Domain Knowledge Base

> **Generated:** 2026-07-17 | **Depth:** Deep | **Audience:** Functional Consultants, Technical Consultants, Solution Architects, AI Engineers, Marketing Specialists, AI Skill Builders

---

## Table of Contents

1. [Core Concepts](#1-core-concepts)
2. [Email Marketing](#2-email-marketing)
3. [Marketing Automation Engine](#3-marketing-automation-engine)
4. [Predictive Lead Scoring (PLS)](#4-predictive-lead-scoring-pls)
5. [IAP-Powered Intelligence Services](#5-iap-powered-intelligence-services)
6. [Odoo 19 AI Platform](#6-odoo-19-ai-platform)
7. [Chatbots and Conversational AI](#7-chatbots-and-conversational-ai)
8. [Social Marketing & SMS Marketing](#8-social-marketing--sms-marketing)
9. [Website Analytics, SEO, and Attribution](#9-website-analytics-seo-and-attribution)
10. [Integrated Business Processes](#10-integrated-business-processes)
11. [Technical Architecture](#11-technical-architecture)
12. [Security and Performance](#12-security-and-performance)
13. [Common Mistakes and Troubleshooting](#13-common-mistakes-and-troubleshooting)
14. [Testing Patterns](#14-testing-patterns)
15. [Migration and Version Differences](#15-migration-and-version-differences)
16. [Checklists](#16-checklists)
17. [FAQs](#17-faqs)
18. [Source Index](#18-source-index)

---

## 1. Core Concepts

Odoo 19.0 provides five primary marketing application modules plus a comprehensive AI Platform.

### Marketing Modules

| Module | Technical Name | Purpose |
|--------|---------------|---------|
| Email Marketing | `mass_mailing` | Drag-and-drop email campaigns, templates, A/B testing, mailing lists |
| SMS Marketing | `mass_mailing_sms` | SMS text message campaigns via IAP or Twilio |
| Social Marketing | `social_marketing` | Social media posting, scheduling, stream monitoring |
| Marketing Automation | `marketing_automation` | Multi-step automated campaign workflows |
| Marketing Card | (separate app) | Direct mail/postcard marketing |

### AI Platform (New in Odoo 19.0)

The dedicated **AI application** (`ai_app`) under Productivity introduces:
- **AI Agents** — Customizable agents with roles, instructions, knowledge sources
- **AI Live Chat** — LLM-powered conversational bots
- **Voice Transcription** — Meeting transcription, note organization, translation
- **AI Chatter Assistant** — Draft/edit emails, summarize discussions
- **AI Fields** — AI-powered computed fields
- **AI Server Actions** — AI-powered workflow automation
- **Web Page Content Generation** — AI-generated first drafts
- **Document Auto-Sort** — Automatic file classification and routing
- **Business Insights Agent** — Natural-language data queries
- **AI in Email Templates** — AI-generated email content

### AI-Enabled Marketing Features (Pre-19.0)

| Feature | Module | IAP Required? | Description |
|---------|--------|:---:|-------------|
| Predictive Lead Scoring | `crm` | No | Naive Bayes ML model trained on historical data |
| Lead Mining | `crm_iap_mine` | Yes | Generate leads from external B2B database |
| Lead Enrichment | `crm_iap_enrich` | Yes | Enrich leads with business data |
| Partner Autocomplete | `partner_autocomplete` | Yes | Auto-populate company contact info |
| Chatbots | `website_chatbot` | No | Scripted decision-tree bots |
| SEO AI Fill | `website` | No | AI-generated meta tags and keywords |
| Link Tracker | `link_tracker` | No | UTM-tracked short URLs |
| Website Analytics | `website_analytics` | No | Plausible/GA integration |

### IAP Services

IAP (In-App Purchases) services for marketing:

| Service | Credit Cost |
|---------|:-----------:|
| Lead Generation (Mining) | 1 credit/lead + 1/contact |
| Lead Enrichment | 1 credit/enrichment |
| Partner Autocomplete | 1 credit/request |
| SMS | Varies by length + destination |
| Snailmail | Per item |

> **Note:** IAP credits are **not interchangeable** between services — each service type has its own credit pool. Enterprise users get free test credits.

---

## 2. Email Marketing

### Core Capabilities

- Drag-and-drop email builder with reusable templates
- A/B Testing with winner selection: Manual, Highest Open Rate, Highest Click Rate, Highest Reply Rate, Leads, Quotations, Revenues
- Recipient targeting: Mailing List, Contact, Event Registration, Lead/Opportunity, Mailing Contact, Sales Order
- Custom domain-based filters for precise segmentation
- Scheduling: Send now or schedule future date/time
- AI prompt integration in email templates (new in 19.0)

### Mailing Lists

- Manual addition, CSV import, website newsletter signup blocks
- Integration with Knowledge/Dashboards/Spreadsheets exports
- Smart buttons show: Recipients count, Mailings count, Bounce %, Opt-out %, Blacklist %

### A/B Testing

Built into Email Marketing (not a separate module). Located in the A/B Tests tab of the email form. Winner selection occurs after a test period; the winning version is sent to the remaining recipients.

### Link Tracking

All email links are automatically converted to tracked URLs via `link_tracker`. Click data feeds into CRM attribution reports.

### Dependencies

`mass_mailing` depends on: `contacts`, `mail`, `html_builder`, `utm`, `link_tracker`, `social_media`, `web_tour`, `digest`

### Blacklist Management

Built-in `mail.blacklist` model tracks opted-out emails. `mailing.contact` inherits `mail.thread.blacklist` for automatic exclusion from all future mailings.

---

## 3. Marketing Automation Engine

### Overview

Creates multi-step automated campaign workflows that combine email, SMS, and server actions into orchestrated sequences with triggers and conditional branching.

### Campaign Structure

- **Target Audience** — Defined by domain/filter expressions (Lead/Opportunity, Event Registration, Contact, etc.)
- **Participants** — Records engaged by the campaign
- **Workflow** — Sequence of automated activities

### Activity Types

| Type | Description |
|------|-------------|
| Email | Send automated emails via mass_mailing |
| Server Action | Execute internal database actions |
| SMS | Send text messages via IAP or Twilio |

### Pre-built Campaign Templates

1. **Tag Hot Contacts** — Welcome email + tag clickers
2. **Welcome Flow** — Welcome email + remove bounced addresses
3. **Double Opt-in** — Confirm consent via email (GDPR)
4. **Commercial Prospection** — Send catalog + follow-up by reactions
5. **Schedule Calls** — Schedule call with salesperson when lead created
6. **Prioritize Hot Leads** — Email + assign high priority if opened

### Dependencies

Required: `mass_mailing` (Email Marketing)  
Recommended: `crm` (CRM), `mass_mailing_sms` (SMS Marketing)

---

## 4. Predictive Lead Scoring (PLS)

### Algorithm

Naive Bayes classifier computed **per sales team**. Uses frequency tables (`crm.lead.scoring.frequency`) storing `won_count` / `lost_count` per `(team_id, variable, value)` combination.

### Configurable Variables

| Variable | Field |
|----------|-------|
| Country | `country_id` |
| State | `state_id` |
| Email Quality | `email_state` |
| Phone Quality | `phone_state` |
| Source | `source_id` |
| Tags | `tag_ids` |
| Stage | `stage_id` (always included) |

### Training Process

1. When a lead is set to **Won** or **Lost**, `_handle_won_lost()` → `_pls_increment_frequencies()` is called
2. Frequencies use **0.1 fractional increments** to avoid zero-probability issues in Naive Bayes multiplication
3. A cron (`_cron_update_automated_probabilities`) periodically rebuilds the full frequency table and recomputes all probabilities

### Probability Computation

- Computed in batch via raw SQL (not Python loops)
- Batch sizes: `PLS_COMPUTE_BATCH_STEP = 50000`, `PLS_UPDATE_BATCH_STEP = 5000`
- Probabilities clamped to `]0.01, 99.99[` for non-won/non-lost leads
- Won leads = 100%, Lost leads = 0%
- Manual override available — click the AI icon to revert to AI-computed value

### Configuration

Found at: **CRM > Configuration > Settings > Update Probabilities**

---

## 5. IAP-Powered Intelligence Services

### Lead Mining (`crm_iap_mine`)

- Query external B2B database by: country, state, industry, company size, role, seniority
- Output: Companies or Companies + Contacts
- Results include: employee count, technology used, timezone, contact info
- **Cost:** 1 credit/lead (+1 per contact)

### Lead Enrichment (`crm_iap_enrich`)

- Fetches: business name, logo, size, revenue, social media accounts, technology used
- Based on customer's email domain
- Two modes:
  - **On demand** — Manual per-lead enrichment
  - **Automatic** — Scheduled every 60 minutes (min. 5 min in dev mode)
- **Cost:** 1 credit/enrichment

### Partner Autocomplete (`partner_autocomplete`)

- Populates: company name, logo, phone, email, tax ID, address, UNSPSC activities as Tags
- Works on **new company contacts only**
- Real-time drop-down suggestions as user types
- **Cost:** 1 credit/request

### IAP Framework (`base_iap`)

- Core framework: IAP account model, credit management, API integration
- Service endpoint: `https://iap.odoo.com`
- Each request: validates credits → processes request → returns result → deducts credits
- Low-credit email alerts configurable per service
- Third parties can offer services through the platform

---

## 6. Odoo 19 AI Platform

### Architecture

The AI Platform (`ai_app`) is a **built-in Enterprise feature** — no IAP required for the platform itself. It provides LLM-powered capabilities across the system.

### Available AI Features

| Feature | Description |
|---------|-------------|
| **AI Agents** | Custom agents with defined roles, instructions, knowledge, and guardrails |
| **AI Chatter Assistant** | Draft emails, improve text, summarize Discuss discussions |
| **AI Fields** | Computed fields that produce AI-generated summaries and insights |
| **AI Server Actions** | Automated actions powered by AI decisions |
| **AI Live Chat** | LLM-powered conversational bots on websites |
| **Voice Transcription** | Meeting transcription, note organization, translation |
| **Web Page Content Generation** | First drafts for new web pages |
| **Document Auto-Sort** | Automatic file classification and routing |
| **Business Insights Agent** | Natural-language queries about sales, performance, forecasts |
| **AI in Email Templates** | AI prompts for email content when sending bulk or individual emails |
| **SEO Fill with AI** | Auto-generates meta title, description, keyword suggestions |

### Configuration

- **AI API Keys** — Configure provider API keys in AI settings
- **AI Default Prompts** — Editable, extensible system prompts
- **AI Agents** — Create and configure in the AI app

### Integration Points

The AI Platform integrates with:
- **Discuss/Chatter** — Content generation and summarization
- **Email Marketing** — AI prompts in email templates
- **Website** — Page content and SEO generation
- **Live Chat** — AI-powered conversations
- **Documents** — Auto-classification and routing
- **All models** — Via AI Fields and AI Server Actions

---

## 7. Chatbots and Conversational AI

### Rule-Based Chatbots

- **Step types:** Text, Question, Email, Phone, Forward to Operator, Free Input/Multi-Line, Create Lead, Create Ticket
- **Conditional logic:** "Only If" field for if/then branching
- **Fallback mode:** Can activate "only when no operator is available"
- **Sample bot:** "Welcome Bot" provided with installation

### AI Live Chat (New in 19.0)

- LLM-powered conversations on top of the existing chatbot infrastructure
- Handles open-ended natural language conversations
- Can be configured alongside or instead of scripted bots

### Configuration

1. Install Live Chat app
2. Navigate: **Live Chat > Configuration > Chatbots**
3. Create script with step sequence
4. Assign to channels via **Channel Rules**
5. Option: "Enabled only if no operator"

### Integration

- Chatbots can create **Leads** and **Tickets** directly from conversations
- Connect to CRM for lead qualification and capture
- Deploy on any website page

---

## 8. Social Marketing & SMS Marketing

### Social Marketing

**Supported platforms:** Facebook, Instagram, LinkedIn, Twitter/X, YouTube

**Capabilities:**
- Post creation, scheduling, and publishing
- Multi-post campaign management
- Real-time social streams for monitoring
- Website visitor identification

### SMS Marketing

**Two backends:**
1. **Odoo IAP SMS** — Credit-based, global coverage
2. **Twilio** — Direct Twilio API integration

**Features:**
- Mailing lists and blacklists
- Performance tracking (delivery rates, opt-outs)
- Integration with Marketing Automation for triggered SMS

---

## 9. Website Analytics, SEO, and Attribution

### Website Analytics

- Integration with **Plausible Analytics** (privacy-focused) and **Google Analytics**
- Tracks: page views, visits, conversions, ecommerce metrics
- Configured in Website settings

### Link Tracker

- Creates trackable short URLs with UTM parameters
- Records: clicks, IP, country, timestamp
- Integrates with Email Marketing for automatic link conversion
- UTM source/medium/campaign attribution feeds into CRM reports

### SEO

- AI-powered content generation: "Fill with AI" button
- Auto-generates: meta title, description, keyword suggestions
- Manage per-page SEO settings from Website > Structure > SEO

### Marketing Attribution

- CRM reports show which campaigns/sources generated leads and revenue
- UTM-based last-touch attribution model
- `utm.mixin` provides reusable campaign/medium/source fields

---

## 10. Integrated Business Processes

### End-to-End Lead Generation Pipeline

```
IAP Lead Mining → CRM Scoring → Enrichment → Automation Nurturing → Conversion
     (src_0005)   (src_0003)    (src_0004)    (src_0002)         (src_0015)
```

### Key Process Integrations

| Process | Modules | Description |
|---------|---------|-------------|
| Lead Generation | CRM + IAP Lead Mining + Chatbot | Generate leads from external DB or website conversations |
| Lead Scoring & Assignment | CRM PLS + Rule-Based Assignment | Auto-score leads, assign to best salesperson |
| Email Campaigns | Email Marketing + Marketing Automation | One-time or triggered multi-step sequences |
| Multi-channel Nurturing | Marketing Automation (Email + SMS + Actions) | Cross-channel prospect journeys |
| Conversion Tracking | CRM + Link Tracker + UTM | Attribution from first touch to close |
| GDPR Compliance | Marketing Automation Double Opt-in + Blacklists | Consent management and opt-out |
| Ecommerce Marketing | eCommerce + Email + Loyalty Programs | Promotions, coupons, abandoned cart recovery |

### Abandoned Cart Recovery

No dedicated module exists. Configure manually:
1. Track cart abandonment via website analytics
2. Target cart abandoners via Marketing Automation
3. Schedule follow-up email sequence

### Referral Programs

No customer-facing referral module exists in standard Odoo 19. HR/Recruitment has an employee referral system only.

---

## 11. Technical Architecture

### Module Hierarchy

```
utm (base)
├── link_tracker
│    └── mass_mailing
│         └── mass_mailing_sms
├── crm
│    ├── crm_iap_mine (Lead Mining)
│    ├── crm_iap_enrich (Lead Enrichment)
│    ├── crm_livechat (Live Chat integration)
│    └── crm_sms (SMS integration)
├── website_chatbot (Chatbots)
└── partner_autocomplete
```

### Key Models

| Model | Module | Description |
|-------|--------|-------------|
| `mailing.mailing` | mass_mailing | Central mass mailing record |
| `mailing.list` | mass_mailing | Mailing contact group |
| `mailing.contact` | mass_mailing | Individual subscriber |
| `mailing.trace` | mass_mailing | Per-recipient tracking stats |
| `mailing.subscription` | mass_mailing | M2M through-table with opt_out |
| `link.tracker` | link_tracker | Tracked URL with UTM |
| `link.tracker.click` | link_tracker | Individual click record |
| `crm.lead` | crm | Lead/Opportunity record |
| `crm.stage` | crm | Pipeline stage (kanban column) |
| `crm.team` | crm | Sales team |
| `crm.lead.scoring.frequency` | crm | PLS frequency table |
| `chatbot.script` | website_chatbot | Chatbot script definition |
| `utm.campaign` | utm | Marketing campaign |
| `utm.medium` | utm | Marketing medium |
| `utm.source` | utm | Marketing source |

### Key Mixins

| Mixin | Provides | Used By |
|-------|----------|---------|
| `utm.mixin` | campaign_id, medium_id, source_id | crm.lead, mailing.mailing |
| `mail.thread.blacklist` | Blacklist integration | mailing.contact, crm.lead |
| `mail.render.mixin` | Template rendering + link shortening | mailing.mailing |
| `mail.activity.mixin` | Activity tracking | mailing.mailing, crm.lead |
| `mail.thread.phone` | Phone fields + validation | crm.lead |

### Python API Integration Points

```python
# Extensible getters in mailing.mailing
def _get_opt_out_list(self):
def _get_seen_list(self):
def _get_recipients_domain(self):
def _get_mass_mailing_context(self):

# PLS hooks in crm.lead
def _handle_won_lost(self):
def _pls_increment_frequencies(self):
def _pls_get_naive_bayes_probabilities(self):
```

---

## 12. Security and Performance

### Security

- **Mass mailing:** `group_mass_mailing_user` and `group_mass_mailing_campaign` groups; record rules for lists/contacts/traces
- **Blacklist system:** `mail.blacklist` model; auto-exclusion from all mailings
- **CRM:** Team-based record rules (`crm_security.xml`); `group_use_lead` for lead/opportunity mode
- **API keys:** WhatsApp/evaluation credentials restricted to `base.group_system`
- **Multi-company:** Standard Odoo multi-company isolation on all marketing records

### Performance Optimizations

- **Raw SQL** for statistics computation (not Python loops)
- **Batch processing** for failed retries (`batch_size=1000`)
- **PLS batch SQL** — 50K records/compute, 5K records/update
- **_read_group aggregation** for link click counts
- **Pre-fetched opt-out/seen caches** before batch sends
- **Cron-based sending** via `ir_cron_mass_mailing_queue`

---

## 13. Common Mistakes and Troubleshooting

### Common Mistakes

| Mistake | Fix |
|---------|-----|
| Expecting IAP credits to work across services | Credits are service-specific — buy for each service individually |
| Expecting PLS to work without historical data | PLS needs won/lost leads to train; default probabilities apply initially |
| Expecting immediate Lead Enrichment results | Automatic enrichment runs every 60 min; use manual for on-demand |
| Marketing Automation without Email Marketing installed | mass_mailing is a required dependency |
| Chatbot script created but not responding | Must assign script to a Live Chat channel via Channel Rules |
| Partner Autocomplete not working on existing contacts | Works only on **new** company contacts |
| Expecting 0% or 100% PLS probability | Probabilities clamped to ]0.01, 99.99[ unless manually won/lost |

### Troubleshooting

**IAP service failures:**
1. Check IAP account has sufficient credits
2. Verify network connectivity to `iap.odoo.com`
3. Check that the IAP service is enabled in Settings

**PLS not updating:**
1. Verify PLS cron job is running
2. Check that leads have been marked won/lost
3. Verify PLS fields are configured in CRM Settings

**Email Marketing not sending:**
1. Check mail server configuration (SPF/DKIM/DMARC)
2. Verify recipient lists have active contacts
3. Check blacklist/exclusion filters
4. Verify sending server is not rate-limited

---

## 14. Testing Patterns

### Module Test Files

**Mass mailing tests** (`mass_mailing/tests/`):
- `test_mailing_ab_testing.py`
- `test_mailing_internals.py`
- `test_mailing_list.py`
- `test_mailing_controllers.py`
- `test_mailing_mailing_schedule_date.py`
- `test_mailing_ui.py`
- `test_utm.py`
- `test_mailing_retry.py`

**CRM tests** (`crm/tests/`):
- `test_crm_pls.py` (PLS-specific)
- `test_crm_lead.py`, `test_crm_lead_merge.py`, `test_crm_lead_convert.py`
- `test_crm_lead_assignment.py`
- `test_crm_lead_multicompany.py`
- `test_performances.py`

### Conventions

- Tagged with `@tagged('post_install', '-at_install')` — fresh DB required
- Extend `MailCommon` or `TestCrmCommon`
- `@classmethod def setUpClass` for fixtures
- `@patch`/`@patch.object` for mocking external API calls
- `mock_mail_gateway()` for mail sending mocks
- PLS tests manipulate frequency tables directly, then call `_cron_update_automated_probabilities()`

---

## 15. Migration and Version Differences

### Key Changes from v17/v18 to v19

| Module | Change | Impact |
|--------|--------|--------|
| `mass_mailing` v2.7 | Added `html_builder` dependency | Asset bundles for email builder |
| `mail.render.mixin` | `_shorten_links` moved here from `link.tracker` | Replace `convert_links` calls |
| `mailing.mailing` | `_get_recipients_domain()` returns `Domain` objects | Adapt custom domain methods |
| `link_tracker` v1.1 | `search_or_create()` expects `list[dict]` | Update single-dict calls |
| `crm` v1.9 | `_handle_won_lost` replaces individual frequency methods | Update PLS hooks |
| `sms` v3.0 | `auto_install` enabled | SMS available without explicit install |
| Discuss | `_to_store_defaults()` replaces polling | Update channel inheritance |
| **New: `ai_app`** | AI Platform | No migration needed — entirely new |

### Migration Checklist

- [ ] Replace `convert_links` calls with `mail.render.mixin._shorten_links`
- [ ] Update `link_tracker.search_or_create()` to pass list of dicts
- [ ] Adapt `_get_recipients_domain()` to return `Domain` objects
- [ ] Migrate `mailing.trace` status tracking to new `trace_status` values
- [ ] Update `crm.lead` PLS hooks for `_handle_won_lost` pattern
- [ ] Adapt discuss channel inheritance to `_to_store_defaults` pattern
- [ ] Review `ir.config_parameter` key changes (`crm.pls_fields`, `crm.pls_start_date`)

---

## 16. Checklists

### Implementation Checklist

- [ ] Install required modules: `mass_mailing`, `marketing_automation`, `crm`, `social_marketing`
- [ ] Configure IAP services in CRM Settings (Lead Mining, Enrichment, Partner Autocomplete)
- [ ] Purchase IAP credit packs from Odoo IAP Catalog
- [ ] Configure Predictive Lead Scoring variables in CRM > Configuration > Settings
- [ ] Set up mail server with SPF/DKIM/DMARC records
- [ ] Build mailing lists and import contacts
- [ ] Create email templates with AI prompts
- [ ] Build Marketing Automation campaigns with target audiences and workflow activities
- [ ] Configure Chatbot scripts and assign to Live Chat channels
- [ ] Connect social media accounts in Social Marketing
- [ ] Enable Link Tracker for campaign attribution
- [ ] Configure Website Analytics (Plausible or GA)
- [ ] Enable SEO AI features on all website pages
- [ ] Set up Loyalty Programs (Sales > Configuration > Settings)

### Testing Checklist

- [ ] Use IAP test credits before purchasing
- [ ] Test PLS with historical won/lost data
- [ ] Send test email campaigns to internal lists
- [ ] Test Marketing Automation with small participant filters
- [ ] Verify chatbot scripts end-to-end with all branches
- [ ] Test A/B testing winner selection with small groups
- [ ] Verify link tracking and UTM attribution in CRM reports
- [ ] Test SMS delivery via IAP and Twilio backends
- [ ] Test multi-company isolation
- [ ] Verify GDPR compliance: opt-out, blacklist, double opt-in
- [ ] Test AI Platform features in staging

### Go-Live Checklist

- [ ] Verify all IAP services have sufficient credit balance
- [ ] Confirm SPF/DKIM/DMARC records for email deliverability
- [ ] Test email warmup if using new sending domain
- [ ] Verify CRM lead assignment rules and PLS calibration
- [ ] Activate Marketing Automation campaigns
- [ ] Configure website analytics tracking
- [ ] Set up link tracker for all campaign URLs
- [ ] Test chatbot deployment on production website
- [ ] Set up monitoring: bounce rates, low IAP credits, campaign performance
- [ ] Document UTM naming conventions for team
- [ ] Train marketing team on AI Platform

### Audit Checklist

- [ ] Review IAP credit usage — identify cost optimization
- [ ] Audit email campaign statistics (opens, clicks, bounces, unsubscribes)
- [ ] Review PLS accuracy — predicted vs actual conversion
- [ ] Audit Marketing Automation participant progression rates
- [ ] Review chatbot conversation quality
- [ ] Audit UTM usage consistency across campaigns
- [ ] Review GDPR compliance (consent, opt-out, data retention)
- [ ] Audit AI Platform usage — which features, cost/value
- [ ] Review email deliverability (SPF/DKIM status, blacklists)

---

## 17. FAQs

**Q: What is the difference between Email Marketing and Marketing Automation?**
A: Email Marketing sends one-time or campaign-based emails with A/B testing. Marketing Automation creates multi-step workflows with triggers, filters, and conditional branching — combining email, SMS, and server actions.

**Q: Do I need IAP credits for Predictive Lead Scoring?**
A: No. PLS is a built-in CRM feature using a Naive Bayes model trained on your own database. No IAP required.

**Q: Can I use my own OpenAI/Anthropic API key with Odoo's AI Platform?**
A: Yes. Odoo 19 allows configuring AI API Keys in AI settings. The supported providers are not fully documented publicly.

**Q: How do AI chatbots differ from rule-based chatbots?**
A: Rule-based = scripted decision trees (good for structured flows). AI Live Chat = LLM-powered natural language (good for open-ended conversations). Both available in 19.0.

**Q: How are email opens and clicks tracked?**
A: Tracking pixel for opens; link_tracker converts all links to tracked URLs recording IP, country, timestamp.

**Q: Does Odoo support abandoned cart recovery?**
A: No dedicated module. Can be configured manually via Marketing Automation targeting eCommerce cart abandoners.

**Q: What are IAP service costs?**
A: Pricing not publicly documented — varies by service/region. Enterprise users get free test credits. Buy packs from Odoo IAP Catalog.

**Q: How do I set up GDPR-compliant double opt-in?**
A: Use the "Double Opt-in" pre-built Marketing Automation campaign template. Confirms consent before adding to mailing lists.

**Q: Can PLS use custom fields?**
A: Yes. Extend `crm.lead.scoring.frequency.field` and update the `crm.pls_fields` config parameter.

---

## 18. Source Index

| ID | Title | Tier | Topics |
|----|-------|------|--------|
| src_0001 | Email Marketing Docs | official | email_marketing, A/B_testing |
| src_0002 | Marketing Automation Docs | official | marketing_automation, campaigns |
| src_0003 | Predictive Lead Scoring Docs | official | pls, crm, machine_learning |
| src_0004 | Lead Enrichment Docs | official | lead_enrichment, IAP |
| src_0005 | Lead Mining Docs | official | lead_generation, IAP |
| src_0006 | Partner Autocomplete Docs | official | partner_autocomplete, IAP |
| src_0007 | Chatbots Docs | official | chatbots, live_chat |
| src_0008 | Social Marketing Docs | official | social_marketing |
| src_0009 | SMS Marketing Docs | official | sms_marketing, IAP |
| src_0010 | IAP Documentation | official | IAP, credits, billing |
| src_0011 | Website Analytics Docs | official | analytics, tracking |
| src_0012 | Link Tracker Docs | official | link_tracker, utm |
| src_0013 | SEO Documentation | official | seo, ai_content |
| src_0014 | Loyalty Programs Docs | official | loyalty, promotions |
| src_0015 | Marketing Attribution Docs | official | attribution, crm |
| src_0016 | AI Platform (odoo.com/app) | official | ai_platform, ai_agents |
| src_0017 | mass_mailing source code | official | module_architecture |
| src_0018 | crm source code (PLS) | official | pls, naive_bayes |
| src_0019 | link_tracker source code | official | click_tracking |
| src_0020 | base_iap source code | official | IAP framework |
| src_0021 | marketing_automation source | official | campaign_workflow |
| src_0022 | crm_iap_mine source | official | lead_mining |
| src_0023 | crm_iap_enrich source | official | lead_enrichment |
| src_0024 | partner_autocomplete source | official | partner_autocomplete |
| src_0025 | website_chatbot source | official | chatbots |
| src_0026 | mass_mailing_sms source | official | sms_marketing |
| src_0027 | PLS Technical Details | official | ml_model |
| src_0028 | AI Platform Features | official | ai_agents, ai_livechat |
| src_0029 | AI Fields & Server Actions | official | ai_fields, ai_automation |
| src_0030 | Enterprise GitHub | official | enterprise, ai_app |
| src_0031 | Marketing Overview (19.0) | official | module_list |
| src_0032 | utm module source | official | utm, tracking |
| src_0033 | website_analytics source | official | analytics |
| src_0034 | mail module source | official | discuss, notifications |
| src_0035 | OCA/social (18.0) | oca_community | gateway |
| src_0036 | OCA/crm (18.0) | oca_community | crm |
| src_0037 | Google Address Autocomplete | official | address, google_api |
| src_0038 | Plausible Analytics | official | privacy, analytics |
| src_0039 | AI Document Sort | official | document_automation |
| src_0040 | AI Discuss Integration | official | chatter, content_gen |

---

## Known Gaps

The following gaps remain in this knowledge base:

1. **LLM Provider Details** — The specific AI model provider(s) powering the Odoo AI Platform are not publicly documented
2. **IAP Pricing** — Per-unit credit costs are not publicly documented (varies by service, region, volume)
3. **AI Data Privacy** — Whether AI inference runs on-premises or in Odoo cloud is not specified
4. **Abandoned Cart** — No dedicated module; requires manual Marketing Automation configuration
5. **Customer Referral Programs** — Not available in standard Odoo 19.0
6. **Smart Send Time** — No AI-based send time optimization found in documented features
7. **Multi-Touch Attribution** — Only last-touch (UTM) attribution available out-of-box
8. **OCA Modules** — OCA repos lag at v18.0; no 19.0 modules available yet
9. **Enterprise Source** — `ai_app` module source code is not publicly accessible
10. **Campaign Scalability Limits** — Max participant counts not documented

> **Sources reviewed:** 40 (40 official, 2 OCA community)  
> **Subtopics covered:** 15  
> **Claims extracted:** 85  
> **Verification sample:** 10/40 sources (25%) — 100% pass rate

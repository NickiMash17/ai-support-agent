# AI Support Agent — Project Reflection

**Student:** Nicolette Mashaba
**Course:** AWS AI & ML Engineer Nanodegree (nd905)
**Project:** AI Support Agent with Amazon Bedrock AgentCore
**Date:** September 2026

---

## Design Decision

The most significant design decision was choosing a **modular tool architecture** built on the Strands SDK rather than a monolithic agent with hardcoded workflows. Each capability — order tracking, refund processing, knowledge base retrieval, loyalty discount calculation, and web browsing — is exposed as an independent tool that the LLM can select and chain dynamically based on user intent.

This decision was driven by the nature of customer support, where requests are inherently non-linear. A user might ask about an order, then pivot to a refund, then ask about loyalty benefits, all within a single conversation. Rather than encoding every possible workflow permutation in code, I let the agent's reasoning determine which tools to invoke. The AgentCore Gateway exposed Lambda-backed tools (order tracker and refund processor) via the Model Context Protocol (MCP), which meant the agent discovered them at runtime without any `@tool` decorators in the agent code. This separation kept the agent code focused purely on orchestration while backend logic remained decoupled and independently maintainable.

I also chose to use Amazon Nova 2 Lite as the foundation model because it offers a strong balance of reasoning capability and cost efficiency, which matters significantly in production customer support workloads where token consumption scales linearly with user volume.

---

## Challenge Encountered

The most significant challenge was an **IAM permission blocker** in the Udacity-provided CloudLab AWS account. The `voc-cancel-cred` policy attached to my CloudLab role explicitly denied all S3 operations (`s3:CreateBucket`, `s3:PutObject`, `s3:ListAllMyBuckets`), which made it impossible to upload the `product_catalog.txt` file required for the Bedrock Knowledge Base. Without S3 access, the entire RAG component of the project could not be built.

I raised a support ticket (#2208419) with Udacity and attempted several workarounds: trying the S3 console UI, the AWS CLI, and CloudShell. All approaches returned the same explicit deny from the `voc-cancel-cred` policy. After 24 hours with no resolution, I made the decision to migrate the project to my personal AWS account, where I had full administrative permissions.

This meant rebuilding the entire infrastructure from scratch: S3 bucket, Knowledge Base with Titan Embeddings v2 and OpenSearch Serverless, AgentCore Memory with semantic and user-preference strategies, both Lambda functions, the API Gateway REST API with three endpoints, and the AgentCore Gateway with API Gateway and Lambda targets.

A secondary challenge emerged during deployment when the agent runtime failed to start with `ModuleNotFoundError: No module named 'strands.tools.browser'`. The correct import path was `strands_tools.browser` (underscore), not `strands.tools.browser` (dot). I diagnosed this through CloudWatch Logs, corrected the import, and redeployed successfully.

**Key lesson:** Validate IAM permissions and required service access early in a cloud project. Build in time for infrastructure blockers and always have a fallback plan.

---

## Production Consideration

For production deployment, the most critical change would be **replacing the "No authorization" Gateway setting with IAM-based authentication**. In this educational environment, the AgentCore Gateway is publicly accessible without access control, which is acceptable for a temporary lab but would be a serious security vulnerability in production. I would enable IAM authorization, require AWS Signature Version 4 (SigV4) request signing for every Gateway invocation, and scope the Gateway's execution role to only the exact Lambda ARNs and API Gateway resources it needs (principle of least privilege).

Beyond security, I would add several operational capabilities:

1. **CloudWatch Alarms** on error rates, latency percentiles, and token consumption to catch degradation before it impacts customers. Alert thresholds would trigger PagerDuty or Slack notifications.

2. **Guardrails for content filtering** to prevent the agent from discussing denied topics (competitors, legal matters, internal disputes) and to redact PII from both inputs and outputs.

3. **Caching for frequently-accessed knowledge base queries** using ElastiCache or DynamoDB Accelerator (DAX). In production, a small set of common questions (return policy, shipping timeframes) accounts for a disproportionate share of traffic, so caching would meaningfully reduce both latency and cost.

4. **Rate limiting at the Gateway level** to prevent abuse and protect the downstream Lambda functions from unexpected traffic spikes.

5. **Structured logging with request IDs** propagated through the Gateway, Lambda, and runtime for end-to-end distributed tracing. This would make debugging production incidents significantly faster.

Finally, I would implement a **CI/CD pipeline** using GitHub Actions to automatically redeploy the agent whenever `main.py` changes, ensuring that updates go through automated testing before reaching production.

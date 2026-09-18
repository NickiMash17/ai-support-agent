# AI Support Agent Project Reflection

- **Student:** Nicolette Mashaba
- **Course:** AWS AI & ML Engineer Nanodegree (nd905)
- **Project:** AI Support Agent with Amazon Bedrock AgentCore
- **Date:** September 2026

## Design Decision

I chose a modular tool architecture using Strands and Amazon Bedrock AgentCore rather than a single agent with hardcoded customer-support workflows. Each capability is a separate tool: order tracking, refund processing, knowledge base retrieval, long-term memory, loyalty calculations, and browser-based research. The agent selects the appropriate tool for each request, so it can support natural multi-step conversations without duplicating backend logic in the prompt. AgentCore Gateway exposes the Lambda-backed order and refund operations through MCP, while the Bedrock Knowledge Base provides grounded product and policy answers. This separation keeps the agent focused on orchestration and makes the individual services easier to test, update, and secure.

## Challenge Encountered

The main challenge was integrating several services with different configuration and protocol requirements. Initial model calls failed because Nova Lite did not reliably format the complex tool-use sequences required by this agent. Switching to the enabled Claude 3 Haiku cross-region inference profile resolved that issue. I then found that Gateway tools are registered with prefixed names, such as `order-tracker___get_order`, rather than the shorter name the model first attempted. I updated the system prompt to make that routing explicit. A separate Knowledge Base error showed that the managed vector store requires `managedSearchConfiguration` instead of the legacy `vectorSearchConfiguration`. Testing each tool independently and reading the exact error messages made it possible to isolate and correct each integration issue.

## Production Consideration

For production, I would replace the Gateway's development-friendly access configuration with IAM authorization and least-privilege roles. I would also add CloudWatch dashboards and alarms for invocation errors, latency, throttling, and tool failures. Customer identifiers and memory content require careful privacy controls, including structured logging that redacts sensitive values and retention policies for stored conversations. Finally, I would add automated tests for each tool route, deployment checks for model access and quotas, and a CI/CD pipeline so changes are validated before deployment.

# **AI Gateway vision** 

Author: [Peter Ableda](mailto:peter.ableda@cloudera.com)  
Date: Feb 13, 2026

---

## **Vision**

To deliver an **infrastructure-aware intelligent control plane** that unifies governance, intelligent routing, and elastic compute anywhere.

### **The Strategic Pillars:**

**A. Governance Fabric**

* **Centralized Control:** A unified policy engine for access control, audit logging, and financial governance across all models.  
* **Universal Guardrails:** Consistent safety and compliance checks applied to both private models and public AI services.

**B. Intelligent Orchestration**

* **Model Routing:** Analyzes input complexity to route prompts to the most cost-effective model dynamically.  
* **Model Arbitration:** Dynamically optimizes your physical deployment footprint based on enterprise-wide usage trends. It automatically scales and runs high-volume models on your local data center hardware to maximize private GPU ROI, while offloading infrequent, low-traffic workloads to pay-per-call public hosted endpoints or autoscaling cloud-based private ai.

**C. Adaptive Infrastructure**

* **Infrastructure-Aware Scaling:** Vertically integrated with inference to monitor GPU saturation and cluster health in real-time.  
* **Cloud Bursting:** Seamlessly spills excess traffic from on-premise private clouds to public cloud resources to ensure strict SLAs.  
  ---

## **1\. The Market Opportunity**

### **The "Shadow AI" Chaos**

Enterprises face tension between velocity and governance. Engineering teams rapidly integrate diverse models, calling OpenAI for one app, hosting Llama models internally for another, and experimenting with Anthropic Claude for a third. This fosters innovation but creates a fragmented **"Shadow AI"** landscape. Security teams lack visibility into data leakage, while FinOps teams struggle to manage API spend across disparate accounts.

### **The "Inference Tax" & The ROI Gap**

As enterprises operationalize private AI, they face a reality: **GPU capacity is finite and expensive.** Without intelligent routing, engineering teams default to deploying the largest open-source models for every use case. This creates an internal "Inference Tax" by using massive, power-hungry compute clusters to answer simple queries or perform basic classification.

### **Saturation & SLA Risk**

Enterprises are repatriating AI workloads to on-premises environments to guarantee data sovereignty and reduce TCO. Unlike elastic public cloud capacity, private compute capacity is fixed. During traffic spikes, standard API gateways continue to route requests to saturated clusters, resulting in severe latency degradation and service timeouts. 

---

## **2\. The Solution: A Unified Control Plane** 

To bridge this gap, we are not just building a gateway; we are building the **intelligent control plane for Enterprise AI.** By providing a flexible control plane that deeply integrates with the underlying inference runtime, we solve three critical fractures in the modern AI stack—whether you are routing to public endpoints or managing private clusters.

### **A. Solving the Trust Deficit (Governance Fabric)**

Instead of forcing developers to wrap every API call in custom security code, the Governance Fabric deploys a Unified AI Firewall. It intercepts every interaction to redact PII, block jailbreak attempts, and enforce RBAC before the request reaches a model. It records every interaction, creating an **immutable audit trail** of prompts and responses for compliance and forensic analysis.

### **B. Solving the Cost Concern (Intelligent Orchestration)**

We treat model selection as a **dynamic marketplace** within your private deployments and public AI. The platform analyzes the "semantic weight" of a prompt to optimize GPU use.

**The Scenario:** A user requests a summary of a short email. Instead of sending to the most intelligent 120B parameter model that requires multiple GPUs, the system routes this to a nimble, quantized 8B model.

### **C. Solving the Scale Wall (Adaptive Infrastructure) — The Cloudera Advantage**

This is our defining differentiator. While generic API gateways act as "dumb pipes" that route traffic without visibility into the underlying hardware, our platform is **infrastructure-aware.** We bridge the gap between the application and compute layers.

**How it works:** The Gateway doesn't just count tokens; it monitors queue depth, GPU saturation, and cluster health in real-time. When your private cluster approaches saturation, we don't just queue requests. We enable **Intelligent Cloud Bursting**, seamlessly spilling excess traffic to public cloud resources to ensure zero downtime.

---

## **3\. Strategic Positioning & Competitive Advantage**

Cloudera is moving beyond the traditional API Gateway to deliver an **Infrastructure-Aware Intelligent Control Plane.** By vertically integrating the control plane with the underlying inference runtime, we unify governance, routing, and compute scaling **anywhere**. No point solution or walled garden can offer this.

### **How We Win**

We are the best platform to unify the three fragmented layers of the AI stack:

**vs. Public AI Vendors (The Walled Gardens)**

* **The Gap:** Vendors such as Azure OpenAI and AWS Bedrock offer robust governance, but only for models within their own ecosystems. They cannot govern a competitor's API or a Llama model running in your private data center.  
* **Our Edge: Universal Sovereignty.** We provide a neutral control plane that governs all your AI—Private and Public—under a single policy framework.

**vs. AI Gateway Providers (The Point Solutions)**

* **The Gap:** Players like Kong or Apigee act as "traffic cops"—they route API requests but have minimal visibility into the underlying GPUs or model runtime. They cannot host models, nor can they scale their infrastructure when queues fill.  
* **Our Edge:** While our Gateway can operate as a neutral broker for public APIs, our ultimate edge is our optional **Vertical Integration**. When paired with our inference runtime, we don't just route traffic; we manage the capacity to fulfill it. **Through Model Arbitration, we actively shift workloads between your private data center and public endpoints based on traffic volume, ensuring you are always operating at maximum infrastructure efficiency.**

**vs. Cloud-Only Data Platforms (The Cloud Purists)**

* **The Gap:** Platforms like Databricks or Snowflake work well for cloud-native AI but can’t deliver the same experience on-premises.   
* **Our Edge: True Hybrid Execution.** We bring the AI to where the data lives. We are the only platform that delivers a consistent operational experience across bare-metal private clouds and public clouds.  
  ---

## **4\. Business Model**

To align with our hybrid technical architecture, we are deploying a **hybrid monetization strategy.** This approach maximizes platform adoption and secures our position as the enterprise's essential Governance Fabric.

* **For models hosted in the AI Inference service, the** Gateway is a critical differentiator. We include the governance fabric to strengthen the value proposition of the AI Inference service and drive growth for our inference revenue.  
* **For external models (e.g., OpenAI, Bedrock, Anthropic):** We capture a "Management Premium." Enterprise customers are willing to pay a markup on third-party model usage to solve the "Shadow AI" fragmentation.

**AI Gateway drives platform gravity** in a market where raw inference is becoming a commodity. We ensure that critical assets such as audit logs, governance policies, and security context are centralized in Cloudera. We are not just storing data; we are **anchoring the customer.** Even when customers use external models from public vendors, they remain dependent on Cloudera for the safety and management layer that enables this usage. Because the Gateway operates independently to capture this external traffic, it acts as a frictionless 'Land' motion. Once centralized, we can seamlessly 'Expand' customers into our AI Inference service, unlocking Advanced Deployment Management and hardware ROI maximization without requiring them to change their application code.

---

---

# **APPENDIX:**

## **Core Capabilities**

The Cloudera AI Gateway is a modular, unified governance layer capable of managing all external API traffic independently. However, when deployed with the Cloudera AI Inference service, it serves as the central security and routing layer, unlocking proprietary, infrastructure-aware deployment automation

The servicesits between users and models, ensuring every interaction is secured, safe, and optimized. The following capabilities represent the platform's target end state. They will be sequenced and delivered in phases to align with customer maturity and engineering velocity.

#### **A. Unified Interface**

Whether a developer calls a secure, privately hosted Llama-3 model or a public GPT-5 endpoint, the API surface remains identical. Our AI Gateway provides an abstraction that enables developers to code against a single standard interface, enabling seamless provider swaps and dynamic inference without requiring application code changes.

PRD: [AI Gateway for Third-Party Model Endpoints](https://docs.google.com/document/d/1Ic1R9NWP-xRdZMtUwRwoIFif7VhtC7RcCwAipc-c7uQ/edit?tab=t.0#heading=h.fvl1oa2wihgq)

#### **B. Security & Access Control**

We replace scattered, provider-specific API keys with a centralized security gate, ensuring developers never handle raw provider credentials directly. We leverage customers' existing identity systems to secure every model interaction; users authorized in the platform gain seamless access to models, eliminating the administrative burden of managing separate credentials.

PRD: [AI Inference service - Authn/Authr requirements](https://docs.google.com/document/d/1yCY6N2iuaEnZ_dky7VkVof_u1e0-RxGaFVw0CFmTWpM/edit?tab=t.0#heading=h.n458zx6jfci)

**C. Audit** 

We capture every model interaction—including prompts, responses, and metadata—and persist them directly into the Cloudera Lakehouse for immutable audit trails and governance. Leveraging Cloudera’s proven big data storage and processing engines ensures that even massive volumes of high-throughput inference logs are ingested reliably for compliance reporting, forensic analysis, and long-term record keeping.

PRD: [CML Serving - Request/Response logging PRFAQ (old)](https://docs.google.com/document/d/1CcqzktUghkOXgmYlMm-Kz1n7BK-HHm4zRdv1XKbU7JU/edit?tab=t.0)

#### **D. Financial Governance ("Tokenomics")**

Unlike standard gateways that count generic HTTP requests, we provide deep visibility into specific token consumption across all internal and external providers, enabling accurate, usage-based chargebacks. Administrators can enforce granular financial policies, such as token quotas and per-team or per-project budget limits, to prevent runaway cloud costs and ensure operational efficiency.

PRD: [AI Gateway for Financial Governance](https://docs.google.com/document/d/1hpzM1cwb0kBZZ1Yi1kDoBLMwNUePKBY53Alna3WUZpw/edit?tab=t.0)  
[Requirements Document: AI Gateway for Financial Governance](https://docs.google.com/document/d/19WEDAoI6IUigQa9GR-BFLtbZn0QO8pNAZcHW5i40n10/edit?tab=t.0#heading=h.w1kaw31zr4iv)

#### **E. Intelligent Routing**

Acting as a smart broker, the Gateway optimizes traffic in real-time by balancing infrastructure constraints against cost.

**Cloud Bursting:** Because the Gateway has deep "infrastructure awareness" (monitoring KV cache and GPU queue depth), it knows exactly when local capacity is exhausted. When enabled, it automatically bursts excess traffic to public AI, ensuring zero downtime for users even during peak demand.

**Cost Arbitrage:** It reduces spend via Model Cascading—routing simple tasks to cost-effective internal SLMs while reserving expensive external LLMs only for complex reasoning.

#### **F. Automated Deployment Management**

Moving beyond per-request routing (Semantic Routing), the Gateway acts as an intelligent scheduler for your entire AI fleet. By analyzing aggregate traffic and usage trends, the system determines exactly *what* should be running *where*. It automatically deploys the highest-volume models onto your fixed internal GPU clusters to absorb the bulk of the cost, while keeping your hardware free from low-volume, specialized workloads that are better suited for external API consumption.

#### **G. AI Safety**

We inject configurable guardrails into the inference path that actively scan and sanitize payloads before they leave the secure perimeter. This includes detecting and redacting Personally Identifiable Information (PII) and potential bias to prevent toxic output or data leakage, ensuring all AI interactions strictly adhere to corporate compliance standards.

## **Customer feedback tracker**

**Axis** **Bank** is using Gemini, Bedrock, and OpenAI. They want to abstract the providers. Trying to implement a workaround where they run “proxy” applications in CAI to interact with the third-party model providers. 

**Porsche** is using Microsoft OpenAI and Bedrock. They want Cloudera to provide a gateway for LLM interactions to reduce their administrative overhead. 

**AbbVie** and **Citibank** have built their own AI Gateway for OpenAI, Gemini, and Amazon Bedrock to unify security and governance for all of their AI usage. Citi is struggling to maintain this critical, central component. 

**OCBC** was tracking LLM Gateway Management as a Must Do requirement in their 2025 AI platform RFI: 

The platform LLM management capabilities provide support for large language model deployment and operation. The LLM gateway and routing system provides intelligent routing to optimal LLMs based on query complexity, performance requirements, and cost considerations. Dynamic distribution of requests across multiple LLM instances ensures optimal resource utilization and performance. Cost optimization features automatically route requests to the most cost-effective models while maintaining quality standards. This capability is particularly important for LLM deployments where computational costs can be significant.

**ItzBund** is tracking “API management, limits or quotas for tokens per minute/budgets and comprehensive monitoring” as critical requirements for their AI Inference platform.

**DXC**’s AI adoption is currently hindered by inconsistent implementation, rising costs, and fragmented governance. Without centralized controls, AI interactions across the enterprise are difficult to monitor and often routed inefficiently, resulting in unnecessary latency and security vulnerabilities. The Need: DXC requires a unified, cost-optimized AI orchestration layer. This solution must ensure every interaction is governed by enterprise policy, protected by robust security standards, and routed to the most efficient model to deliver accurate, unbiased results at scale. [DXC - AI Gateway - Problem staement & use cases.pdf](https://drive.google.com/file/d/1el-KehraQO6LnxqEXBkIVmNmFn-om3lf/view?usp=sharing)

**Telecom Argentina** is looking for an AI Gateway that can provide dynamic routing to LLMs from various cloud providers (GCP, AWS, Azure) and deployment environments (SaaS, on-premises, or hybrid). The Gateway must offer a single entry point for all models. Key requirements include robust operational features such as HA, monitoring and traceability, usage and cost reporting, and management of retries/fallback. The solution must support complex agentic interaction patterns (chained invocations, external tools) and multi-step execution flows, including full traceability. Security must be ensured through compliance with data protection regulations, encryption (in transit and at rest), and integration with corporate secret and identity management. [Telecom Argentina RFP Gateway LLMs.pdf](https://drive.google.com/file/d/1nvx-F7ellD8O-garolmQVsVLj6wAD4Mh/view?usp=sharing)

From [Ryan Hill](mailto:rhill@cloudera.com): “This could help with customers like **Disney** that have a variety of bespoke internal endpoints to integrate with, both by reducing the need to implement compatible integrations with each of Cloudera’s services, and by simplifying the development process for the Customer’s teams as they may also use frameworks which expect standard endpoint APIs.”

From: [Sreenath Somarajapuram](mailto:ssomarajapuram@cloudera.com): “Many of our customers who are trying out SQL AI are asking for simpler configuration, better access control, and short-term API key support when working with Azure or Bedrock. AI Gateway could have solved all those concerns.”   
AI Gateway for SQL AI came up as a requirement at **Navyfederal**, **Zeta,** and **Marigold.** 

**Mastercard:** They are seeking an "intelligent layer" to automatically determine what to run in their data center, specifically focusing on tight-packing their GPUs with the most heavily utilized models. They conceptualized this as needing an "agent" to manage the workload automation. Currently, they are using a competitor's AI gateway outside of Cloudera, but it lacks this infrastructure-aware deployment capability. To bridge this gap, they are planning to build their own custom agent to monitor gateway traffic and execute model deployment decisions themselves. They were unaware that Cloudera was developing an integrated solution and expressed strong interest in learning more.

JPMC
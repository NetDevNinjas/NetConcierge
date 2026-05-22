---
title: Copy of TIP SDK Documentation
description: General documentation for Travel Innovation Platform (TIP) SDK
---

# TIP SDK Documentation

The TIP SDK is a multi-platform toolkit for building AI-powered applications on the TIP platform. It provides a unified
interface for LLM providers through LiteLLM with built-in support for agents, tools, MCP, and enterprise features.

*** ** * ** ***

## Available Platforms

|     Platform     |        Package         |                           Use Case                           |
|------------------|------------------------|--------------------------------------------------------------|
| **Command Line** | `tip-cli`              | Interactive AI chat, agent testing, configuration management |
| **Python**       | `tip-sdk`              | Web apps, data science, scripting, automation                |
| **Node.js**      | `@tip/sdk`             | Full-stack JavaScript, serverless functions, TypeScript apps |
| **Java**         | `com.tip:tip-sdk-java` | Enterprise applications, Spring Boot, Android, JVM languages |
| **Rust**         | `tip-sdk`              | High-performance services, systems programming, CLI tools    |

*** ** * ** ***

## Getting Started

Choose your platform:

* **CLI** --- `tip config init` to get started with the command-line tool

* **Python** --- `pip install tip-sdk` or `uv add tip-sdk`

* **Node.js** --- `npm install @tip/sdk`

* **Java** --- Add `com.tip:tip-sdk-java` to your Gradle/Maven build

* **Rust** --- Add `tip-sdk` to your `Cargo.toml`

See **Installation \& Configuration** for detailed setup instructions including corporate CA certificates.

*** ** * ** ***

## Core Features

All SDK platforms share these capabilities:

|         Feature         |                                   Description                                    |
|-------------------------|----------------------------------------------------------------------------------|
| **LiteLLM Integration** | Unified interface for multiple LLM providers (OpenAI, Bedrock, Azure, Anthropic) |
| **Agent Framework**     | Build intelligent agents with tool calling and orchestration                     |
| **MCP Support**         | Model Context Protocol integration for extensible tool ecosystems                |
| **RAG Primitives**      | Chunking, embeddings, and knowledge management                                   |
| **Guardrails**          | Input/output validation, PII detection, injection prevention                     |
| **Observability**       | Built-in tracing, metrics, and logging                                           |
| **Human-in-the-Loop**   | Approval workflows for sensitive operations                                      |
| **Streaming**           | Real-time token streaming for all platforms                                      |

*** ** * ** ***

## Quick Examples

### CLI

```
# Interactive chat
tip chat

# One-shot completion
tip chat -m "What is the capital of France?"

# Use a specific model
tip chat --model anthropic.claude-sonnet-4-20250514-v1:0

# Test an agent
tip agents run my-agent --input "Search for recent news"
```

### Python

```
from tip_sdk import TipClient

client = TipClient()
response = client.chat.completions.create(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### Node.js

```
import { TipClient } from '@tip/sdk';

const client = new TipClient();
const response = await client.chat.completions.create({
    model: 'anthropic.claude-sonnet-4-20250514-v1:0',
    messages: [{ role: 'user', content: 'Hello!' }]
});
console.log(response.choices[0].message.content);
```

### Java

```
import com.tip.sdk.*;
import java.util.List;

// Create client (reads TIP_API_KEY from environment)
try (TipClient client = new TipClient()) {
    ChatCompletion response = client.complete(List.of(
        Message.user("Hello!")
    ));
    System.out.println(response.getContent());
}

// Or use the builder for custom configuration
TipClient client = TipClient.builder()
    .apiKey("your-api-key")
    .defaultModel("anthropic.claude-sonnet-4-20250514-v1:0")
    .build();

// Async with CompletableFuture
CompletableFuture<ChatCompletion> future = client.completeAsync(
    List.of(Message.user("Hello!"))
);
future.thenAccept(r -> System.out.println(r.getContent()));
```

### Rust

```
use tip_sdk::TipClient;

#[tokio::main]
async fn main() {
    let client = TipClient::new();
    let response = client.chat().completions()
        .create(CreateChatCompletionRequest {
            model: "anthropic.claude-sonnet-4-20250514-v1:0".into(),
            messages: vec![Message::user("Hello!")],
            ..Default::default()
        })
        .await
        .unwrap();
    println!("{}", response.choices[0].message.content);
}
```

*** ** * ** ***

## Documentation Sections

### Overview

* Purpose

* Why an SDK

### Installation \& Configuration

* Install (CLI, Python, Node.js, Java, Rust)

* Configuration

* Corporate Environment Considerations (Certificates)

### Quick Start

* Hello TIP

* Your First Agent

* Basic Application

### Reference

* Architecture

* Security Documentation

* CI/CD Integration

* Concepts \& Best Practices

### API Reference

* **Python** : Package documentation for `tip_sdk.*` modules

* **Node.js**: TypeScript type definitions and module docs

* **Java**: Javadoc and package documentation

* **Rust** : Crate documentation via `cargo doc`

* **CLI** : `tip --help` and subcommand help

*** ** * ** ***

## Environment Variables

All platforms use consistent environment variables:

|      Variable       |           Description           |                  Default                   |
|---------------------|---------------------------------|--------------------------------------------|
| `TIP_API_KEY`       | API key for authentication      | (required)                                 |
| `TIP_BASE_URL`      | LiteLLM endpoint URL            | TIP production endpoint                    |
| `TIP_DEFAULT_MODEL` | Default model for completions   | `anthropic.claude-haiku-4-5-20251001-v1:0` |
| `TIP_TIMEOUT`       | Request timeout in seconds      | `60`                                       |
| `TIP_KNOWLEDGE_URL` | Knowledge service endpoint      | (optional)                                 |
| `TIP_TEMPORAL_URL`  | Temporal endpoint for workflows | (optional)                                 |

*** ** * ** ***

## Source Code

The SDK is developed as a unified monorepo:

**Repository** : [git.marriott.com/emerging-tech/tip-sdk](https://git.marriott.com/emerging-tech/tip-sdk)

|     Crate      |         Path          |        Description         |
|----------------|-----------------------|----------------------------|
| `tip-sdk`      | `crates/tip-sdk`      | Core Rust SDK              |
| `tip-cli`      | `crates/tip-cli`      | Command-line interface     |
| `tip-sdk-py`   | `crates/tip-sdk-py`   | Python bindings (PyO3)     |
| `tip-sdk-js`   | `crates/tip-sdk-js`   | Node.js bindings (NAPI-RS) |
| `tip-sdk-java` | `crates/tip-sdk-java` | Java bindings (JNI)        |

*** ** * ** ***

## Contributions and Bug Reports

See **Contributions and Bug Reports** for guidelines on reporting issues and contributing to the SDK.

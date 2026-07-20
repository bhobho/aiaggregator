"""Curated learning & reference content for the Resources tab.

No RSS feed exists for playbooks, glossaries, or learning paths, so this is
authored, static data. Kept as plain dataclasses so templates stay dumb.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PathStep:
    title: str
    detail: str


@dataclass(frozen=True)
class LearningPath:
    slug: str
    name: str
    emoji: str
    audience: str
    summary: str
    steps: tuple[PathStep, ...]


LEARNING_PATHS: tuple[LearningPath, ...] = (
    LearningPath(
        "ai-engineer", "AI Engineer Path", "💻",
        "Software & AI/ML Engineers",
        "Ship reliable LLM-powered features end to end.",
        (
            PathStep("Foundations of LLMs",
                     "Tokens, context windows, prompting, temperature, and cost."),
            PathStep("Retrieval-Augmented Generation",
                     "Chunking, embeddings, vector stores, and grounding answers."),
            PathStep("Agents & tool use",
                     "Function calling, planning loops, and orchestration frameworks."),
            PathStep("Evaluation & guardrails",
                     "Offline evals, tracing, red-teaming, and safety filters."),
            PathStep("Production & ops",
                     "Latency, caching, streaming, observability, and rollout."),
        ),
    ),
    LearningPath(
        "ai-architect", "AI Architect Path", "🏛️",
        "Solution & Enterprise Architects",
        "Design scalable, governable AI systems.",
        (
            PathStep("Reference architectures",
                     "RAG, agentic, and event-driven AI patterns."),
            PathStep("Model strategy",
                     "Hosted vs open-weight, routing, and build-vs-buy trade-offs."),
            PathStep("Data & retrieval design",
                     "Pipelines, vector + hybrid search, and freshness."),
            PathStep("Security & governance",
                     "Access control, PII handling, auditability, and policy."),
            PathStep("Scale & cost",
                     "Inference infra, autoscaling, and FinOps for AI."),
        ),
    ),
    LearningPath(
        "ai-product-leader", "AI Product Leader Path", "🚀",
        "Product Managers & Innovation Leads",
        "Turn AI capability into customer value.",
        (
            PathStep("Opportunity framing",
                     "Where AI creates defensible value vs novelty."),
            PathStep("Use-case discovery",
                     "Jobs-to-be-done, feasibility, and prioritization."),
            PathStep("Experiment design",
                     "Prototyping, human-in-the-loop, and success metrics."),
            PathStep("Trust & UX",
                     "Transparency, fallback design, and managing hallucination."),
            PathStep("Adoption & ROI",
                     "Rollout, measurement, and scaling what works."),
        ),
    ),
    LearningPath(
        "ai-executive", "AI Executive Path", "📈",
        "CTOs, CIOs, CEOs & Founders",
        "Set AI strategy and manage the risk.",
        (
            PathStep("The AI landscape",
                     "Labs, models, and where the frontier is heading."),
            PathStep("Strategy & competition",
                     "Where to invest, partner, or wait."),
            PathStep("Governance & risk",
                     "Regulation, security, and responsible-AI posture."),
            PathStep("Org & talent",
                     "Operating model, skills, and change management."),
            PathStep("Measuring impact",
                     "ROI, portfolio management, and board-level reporting."),
        ),
    ),
)


@dataclass(frozen=True)
class Resource:
    title: str
    kind: str        # Playbook | Guide | Template | Report
    blurb: str


RESOURCES: tuple[Resource, ...] = (
    Resource("Enterprise AI Adoption Playbook", "Playbook",
             "A stage-by-stage plan from pilot to production, with governance gates."),
    Resource("RAG Architecture Guide", "Guide",
             "Reference design for retrieval-augmented generation at scale."),
    Resource("Agent System Design Guide", "Guide",
             "Patterns for planning, tool use, memory, and evaluation."),
    Resource("Build vs Buy Decision Template", "Template",
             "A scoring framework for model and platform sourcing decisions."),
    Resource("AI Risk & Governance Checklist", "Template",
             "Controls for security, privacy, and responsible-AI compliance."),
    Resource("Model Evaluation Report Template", "Template",
             "A structured format for benchmarking and comparing models."),
)


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    definition: str


GLOSSARY: tuple[GlossaryTerm, ...] = (
    GlossaryTerm("LLM", "Large Language Model — a neural network trained on vast "
                        "text to predict and generate language."),
    GlossaryTerm("RAG", "Retrieval-Augmented Generation — grounding a model's "
                        "answers in retrieved documents to reduce hallucination."),
    GlossaryTerm("Agent", "An AI system that plans, calls tools, and takes multi-step "
                          "actions toward a goal rather than replying once."),
    GlossaryTerm("Fine-tuning", "Further training a base model on task-specific data "
                                "to specialize its behavior."),
    GlossaryTerm("Embedding", "A numeric vector representing text or media so that "
                              "similar items sit close together in vector space."),
    GlossaryTerm("Token", "The unit of text a model reads and generates; billing and "
                          "context limits are measured in tokens."),
    GlossaryTerm("Context window", "The maximum amount of text (in tokens) a model "
                                   "can consider at once."),
    GlossaryTerm("MCP", "Model Context Protocol — an open standard for connecting "
                        "models to tools and data sources."),
    GlossaryTerm("Multimodal", "A model that handles more than one modality — e.g. "
                               "text, images, audio, and video together."),
    GlossaryTerm("Inference", "Running a trained model to produce outputs; the "
                              "ongoing compute cost of serving AI."),
    GlossaryTerm("Quantization", "Compressing model weights to lower precision to cut "
                                 "memory and speed up inference."),
    GlossaryTerm("Hallucination", "When a model produces fluent but false or "
                                  "unsupported information."),
)

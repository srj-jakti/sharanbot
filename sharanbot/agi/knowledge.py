"""Knowledge Accumulation — proactive learning like a human.

A human reads docs, watches tutorials, and builds expertise over time.
SharanAGI does the same:

1. **Study Mode** — reads documentation and stores key knowledge
2. **Technical Knowledge Base** — persistent domain knowledge (not just
   user preferences like the subconscious)
3. **Skill Self-Assessment** — knows what it's good at and what it needs
   to learn before attempting unfamiliar tasks
4. **Cross-Session Expertise** — accumulated technical knowledge that
   grows over time and is injected into prompts
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from sharanbot.providers.base import LLMProvider


@dataclass
class KnowledgeEntry:
    """A single piece of learned knowledge."""

    topic: str
    content: str
    source: str = ""  # URL, file path, or "experience"
    confidence: float = 0.8  # 0.0 to 1.0
    times_used: int = 0
    last_used: float = 0.0
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "times_used": self.times_used,
            "last_used": self.last_used,
            "created_at": self.created_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SkillAssessment:
    """Self-assessment of competence in a domain."""

    domain: str
    level: str  # novice, intermediate, proficient, expert
    confidence: float  # 0.0 to 1.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    last_assessed: float = field(default_factory=time.time)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "level": self.level,
            "confidence": self.confidence,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "last_assessed": self.last_assessed,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillAssessment:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class KnowledgeBase:
    """Persistent technical knowledge base that grows over time.

    Unlike the subconscious (which tracks user preferences and session
    patterns), the KnowledgeBase stores technical domain knowledge:
    - How to use specific frameworks/libraries
    - Architecture patterns that worked
    - Common pitfalls and solutions
    - API quirks and workarounds
    """

    _FILENAME = "knowledge_base.json"
    _MAX_ENTRIES = 500

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._path = workspace / self._FILENAME
        self._entries: dict[str, KnowledgeEntry] = {}
        self._skills: dict[str, SkillAssessment] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for edata in data.get("entries", []):
                entry = KnowledgeEntry.from_dict(edata)
                self._entries[entry.topic] = entry
            for sdata in data.get("skills", []):
                skill = SkillAssessment.from_dict(sdata)
                self._skills[skill.domain] = skill
        except Exception as e:
            logger.warning("Failed to load knowledge base: {}", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "entries": [e.to_dict() for e in self._entries.values()],
                "skills": [s.to_dict() for s in self._skills.values()],
                "saved_at": time.time(),
            }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save knowledge base: {}", e)

    # ------------------------------------------------------------------
    # Knowledge CRUD
    # ------------------------------------------------------------------

    def learn(
        self,
        topic: str,
        content: str,
        source: str = "experience",
        confidence: float = 0.8,
        tags: list[str] | None = None,
    ) -> KnowledgeEntry:
        """Store a new piece of knowledge or update existing."""
        existing = self._entries.get(topic)
        if existing:
            # Merge: append new content, increase confidence
            existing.content = f"{existing.content}\n\n---\n{content}"
            if len(existing.content) > 5000:
                existing.content = existing.content[-5000:]
            existing.confidence = min(1.0, existing.confidence + 0.1)
            existing.source = source
            if tags:
                existing.tags = list(set(existing.tags + tags))
            entry = existing
        else:
            entry = KnowledgeEntry(
                topic=topic, content=content[:5000],
                source=source, confidence=confidence,
                tags=tags or [],
            )
            self._entries[topic] = entry

        # Evict oldest if over limit
        if len(self._entries) > self._MAX_ENTRIES:
            oldest = min(self._entries.values(), key=lambda e: e.last_used or e.created_at)
            del self._entries[oldest.topic]

        self._save()
        return entry

    def recall(self, query: str, max_results: int = 5) -> list[KnowledgeEntry]:
        """Search knowledge base for relevant entries."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored: list[tuple[float, KnowledgeEntry]] = []
        for entry in self._entries.values():
            score = 0.0
            topic_lower = entry.topic.lower()
            content_lower = entry.content.lower()

            # Exact topic match
            if query_lower in topic_lower:
                score += 10.0
            # Word overlap with topic
            topic_words = set(topic_lower.split())
            overlap = len(query_words & topic_words)
            score += overlap * 3.0
            # Tag match
            for tag in entry.tags:
                if tag.lower() in query_lower:
                    score += 5.0
            # Content match
            if query_lower in content_lower:
                score += 2.0
            # Recency and usage boost
            if entry.times_used > 0:
                score += min(entry.times_used * 0.5, 3.0)

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        results = [entry for _, entry in scored[:max_results]]

        # Mark as used
        for entry in results:
            entry.times_used += 1
            entry.last_used = time.time()
        if results:
            self._save()

        return results

    def get_entry(self, topic: str) -> KnowledgeEntry | None:
        return self._entries.get(topic)

    def list_topics(self) -> list[str]:
        return list(self._entries.keys())

    def forget(self, topic: str) -> bool:
        if topic in self._entries:
            del self._entries[topic]
            self._save()
            return True
        return False

    # ------------------------------------------------------------------
    # Skill self-assessment
    # ------------------------------------------------------------------

    def assess_skill(self, domain: str) -> SkillAssessment:
        """Get or create a skill assessment for a domain."""
        if domain not in self._skills:
            self._skills[domain] = SkillAssessment(
                domain=domain, level="novice", confidence=0.3,
            )
            self._save()
        return self._skills[domain]

    def record_task_outcome(self, domain: str, success: bool) -> SkillAssessment:
        """Record a task outcome to update skill assessment."""
        skill = self.assess_skill(domain)
        if success:
            skill.tasks_completed += 1
            skill.confidence = min(1.0, skill.confidence + 0.05)
        else:
            skill.tasks_failed += 1
            skill.confidence = max(0.0, skill.confidence - 0.1)

        # Update level based on success rate
        total = skill.tasks_completed + skill.tasks_failed
        if total >= 3:
            rate = skill.tasks_completed / total
            if rate >= 0.9 and total >= 10:
                skill.level = "expert"
            elif rate >= 0.75 and total >= 5:
                skill.level = "proficient"
            elif rate >= 0.5:
                skill.level = "intermediate"
            else:
                skill.level = "novice"

        skill.last_assessed = time.time()
        self._save()
        return skill

    def should_study_before_task(self, task: str) -> list[str]:
        """Check if the agent should study before attempting a task.

        Returns a list of domains that need studying (low confidence).
        """
        domains_to_study = []
        task_lower = task.lower()

        # Map keywords to domains
        domain_keywords = {
            "kubernetes": ["kubernetes", "k8s", "kubectl", "helm", "pod", "deployment"],
            "docker": ["docker", "container", "dockerfile", "compose"],
            "react": ["react", "jsx", "tsx", "component", "hook", "useState"],
            "vue": ["vue", "vuex", "nuxt", "composition api"],
            "rust": ["rust", "cargo", "borrow checker", "lifetime"],
            "go": ["golang", "goroutine", "go mod"],
            "aws": ["aws", "lambda", "s3", "ec2", "cloudformation", "cdk"],
            "database": ["sql", "postgres", "mysql", "mongodb", "redis"],
            "ml": ["machine learning", "tensorflow", "pytorch", "model training"],
            "security": ["oauth", "jwt", "encryption", "vulnerability", "pentest"],
        }

        for domain, keywords in domain_keywords.items():
            if any(kw in task_lower for kw in keywords):
                skill = self._skills.get(domain)
                if not skill or skill.confidence < 0.5:
                    domains_to_study.append(domain)

        return domains_to_study

    def list_skills(self) -> list[SkillAssessment]:
        return list(self._skills.values())

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def build_knowledge_context(self, task: str) -> str:
        """Build a knowledge context string for injection into prompts."""
        relevant = self.recall(task, max_results=3)
        if not relevant:
            return ""

        parts = ["[Knowledge Base — accumulated technical expertise]"]
        for entry in relevant:
            parts.append(
                f"<knowledge topic=\"{entry.topic}\" confidence={entry.confidence:.1f}>\n"
                f"{entry.content[:1000]}\n"
                f"</knowledge>"
            )
        return "\n\n".join(parts)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "total_skills": len(self._skills),
            "top_skills": [
                {"domain": s.domain, "level": s.level, "confidence": s.confidence}
                for s in sorted(self._skills.values(), key=lambda s: -s.confidence)[:5]
            ],
        }


_STUDY_PROMPT = """\
You are a knowledge extraction agent. Read the following content and extract
the most important technical knowledge that would be useful for a developer.

Content source: {source}
Content:
{content}

Extract knowledge as a JSON object:
{{
  "entries": [
    {{
      "topic": "short topic name",
      "content": "key knowledge (max 500 chars)",
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

Rules:
- Extract 1-5 entries max
- Focus on practical, actionable knowledge (API patterns, gotchas, config)
- Skip generic/obvious information
- Each entry should be self-contained and useful out of context
- Output ONLY valid JSON"""


class StudyAgent:
    """Agent that reads documentation and extracts knowledge.

    Usage:
        study = StudyAgent(provider, model, knowledge_base)
        await study.study_url("https://docs.example.com/api")
        await study.study_text("...", source="local docs")
    """

    def __init__(self, provider: LLMProvider, model: str, kb: KnowledgeBase):
        self._provider = provider
        self._model = model
        self._kb = kb

    async def study_text(self, content: str, source: str = "unknown") -> list[str]:
        """Study a text and extract knowledge entries. Returns topic names."""
        if len(content) < 50:
            return []

        # Truncate very long content
        if len(content) > 8000:
            content = content[:8000] + "\n... (truncated)"

        prompt = _STUDY_PROMPT.format(source=source, content=content)
        messages = [
            {"role": "system", "content": "You are a knowledge extraction specialist. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self._provider.chat_with_retry(
                messages=messages, model=self._model,
            )
            return self._apply_study_results(response.content or "", source)
        except Exception as e:
            logger.warning("Study agent failed: {}", e)
            return []

    def _apply_study_results(self, response_text: str, source: str) -> list[str]:
        """Parse study results and store in knowledge base."""
        topics = []
        try:
            text = response_text.strip()
            if "```" in text:
                import re
                match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
                if match:
                    text = match.group(1).strip()

            data = json.loads(text)
            for entry_data in data.get("entries", []):
                topic = entry_data.get("topic", "").strip()
                content = entry_data.get("content", "").strip()
                tags = entry_data.get("tags", [])
                if topic and content:
                    self._kb.learn(
                        topic=topic, content=content,
                        source=source, tags=tags,
                    )
                    topics.append(topic)
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("Study agent: failed to parse results: {}", e)
        return topics

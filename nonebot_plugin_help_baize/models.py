from dataclasses import dataclass, field
from typing import List


@dataclass
class HelpEntry:
    plugin_id: str
    display_name: str
    description: str
    usage_lines: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    category: str = "其他"
    source_path: str = ""

    @property
    def search_blob(self) -> str:
        parts = [
            self.plugin_id,
            self.display_name,
            self.description,
            " ".join(self.usage_lines),
            " ".join(self.commands),
            " ".join(self.notes),
            " ".join(self.examples),
            self.category,
        ]
        return " ".join(parts).lower()


@dataclass
class HelpQueryResult:
    title: str
    subtitle: str
    entries: List[HelpEntry] = field(default_factory=list)
    keyword: str = ""

from dataclasses import dataclass
from typing import Optional

from dto import AnswerEvaluation


@dataclass
class Answer:
		message: str
		evaluation: Optional[AnswerEvaluation] = None
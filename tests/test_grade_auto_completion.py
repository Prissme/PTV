import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cogs import grades  # noqa: E402
from cogs.grades import GradeSystem  # noqa: E402


class _FakeRecord(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class _DummyChannel:
    def __init__(self) -> None:
        self.sent_embeds: list = []

    async def send(self, *, embed) -> None:  # pragma: no cover - exercised via grade command
        self.sent_embeds.append(embed)


class _DummyDM(_DummyChannel):
    pass


class _DummyMember:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.display_name = f"User {user_id}"
        self.display_avatar = SimpleNamespace(url="https://example.com/avatar.png")
        self.guild = None

    async def create_dm(self) -> _DummyDM:  # pragma: no cover - simple stub
        return _DummyDM()


class _DummyContext:
    def __init__(self, author: _DummyMember) -> None:
        self.author = author
        self.channel = _DummyChannel()
        self.sent_embeds: list = []

    async def send(self, *, embed) -> None:
        self.sent_embeds.append(embed)


class _FakeDatabase:
    def __init__(self) -> None:
        self.grade_level = 0
        self.progress = {
            "mastermind_progress": 0,
            "egg_progress": 3,
            "sale_progress": 1,
            "potion_progress": 1,
        }

    async def get_user_grade(self, _user_id: int) -> _FakeRecord:
        return _FakeRecord({"grade_level": self.grade_level, **self.progress})

    async def complete_grade_if_ready(
        self,
        _user_id: int,
        *,
        mastermind_goal: int,
        egg_goal: int,
        sale_goal: int,
        potion_goal: int,
        max_grade: int,  # noqa: ARG002 - conforms to real signature
    ):
        ready = (
            self.progress["mastermind_progress"] >= mastermind_goal
            and self.progress["egg_progress"] >= egg_goal
            and self.progress["sale_progress"] >= sale_goal
            and self.progress["potion_progress"] >= potion_goal
        )
        if ready and self.grade_level < max_grade:
            self.grade_level += 1
            self.progress = {
                "mastermind_progress": 0,
                "egg_progress": 0,
                "sale_progress": 0,
                "potion_progress": 0,
            }
            return True, _FakeRecord({"grade_level": self.grade_level, **self.progress})
        return False, _FakeRecord({"grade_level": self.grade_level, **self.progress})

    async def increment_balance(self, *_args, **_kwargs):  # pragma: no cover - simple stub
        return 0, 0

def test_grade_command_auto_claims_ready_grade(monkeypatch) -> None:
    monkeypatch.setattr(grades.discord, "Member", _DummyMember, raising=False)

    bot = SimpleNamespace(
        database=_FakeDatabase(),
        guilds=[],
        dispatch=lambda *args, **kwargs: None,
    )
    grade_cog = GradeSystem(bot)
    member = _DummyMember(42)
    ctx = _DummyContext(member)

    asyncio.run(grade_cog.grade_command.callback(grade_cog, ctx))

    assert bot.database.grade_level == 1
    assert ctx.channel.sent_embeds, "grade completion notification should be sent"
    assert ctx.sent_embeds, "grade profile embed should be sent"

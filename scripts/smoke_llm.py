"""Show runtime model configuration. --live opts into one synthetic model call."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import select
from hiring.ai_questions import AiDraftBody, create_draft
from hiring.config import Config
from hiring.db import User, connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', choices=['junior', 'middle', 'senior'], default='junior')
    parser.add_argument('--live', action='store_true', help='Allow one provider request using synthetic skills')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env.hiring')
    config = Config(worker=False, bot_token='')
    config.llm.validate()
    print(f'provider={config.llm.provider}; model={config.llm.model or "none"}; ready={config.llm.ready}')
    if not args.live:
        print('Configuration only. No network request. Pass --live for a bounded synthetic check.')
        return 0
    if not config.llm.ready:
        print('Provider disabled or key missing. No request made.')
        return 1
    engine, factory = connect('sqlite:///' + (root / 'data/hiring.ai-smoke.db').as_posix())
    with factory() as db:
        owner = db.scalar(select(User).where(User.email == 'runtime-smoke@example.invalid'))
        if not owner:
            owner = User(email='runtime-smoke@example.invalid', name='Synthetic runtime check', role='employer')
            db.add(owner)
            db.commit()
        result = create_draft(db, owner.id, AiDraftBody(skills=['python', 'sql'], level=args.level,
                               allow_external_generation=True), config)
        print(f'source={result.source}; cached={result.cached}; questions={len(result.draft.questions)}; published={result.published}')
        print('No applicant data or development prompt sent. Key not printed.')
    engine.dispose()
    return 0 if result.provider else 2


if __name__ == '__main__':
    sys.exit(main())

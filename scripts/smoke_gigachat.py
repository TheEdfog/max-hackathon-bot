"""Explicit, bounded runtime smoke on synthetic skills; never a coding helper."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
from sqlalchemy import select
from hiring.ai_questions import AiDraftBody, create_draft
from hiring.config import Config
from hiring.db import User, connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', choices=['junior', 'middle', 'senior'], default='junior')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    key = dotenv_values(root / '.env.hiring').get('CLOUDRU_API_KEY', '')
    if not key:
        print('Missing local runtime key. No request made.')
        return 1
    engine, factory = connect('sqlite:///' + (root / 'data/hiring.ai-smoke.db').as_posix())
    config = Config(gigachat_enabled=True, cloudru_api_key=key, worker=False, bot_token='')
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
    return 0 if result.source == 'gigachat' else 2


if __name__ == '__main__':
    sys.exit(main())

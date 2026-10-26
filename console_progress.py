"""Progress display from committed decisions of the same run, including resumed runs."""
import db


def prefix(run_id,target):
    with db.get_connection() as c:
        row=c.execute('SELECT count(*),COALESCE(sum(accepted),0) FROM collection_decisions WHERE run_id=?',(run_id,)).fetchone()
    return f'[{row[0]:03d}] [Готово: {row[1]}/{target}]'

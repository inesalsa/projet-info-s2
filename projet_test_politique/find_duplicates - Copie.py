from app import app  # importe ton app Flask
from models import Article, Question, db

with app.app_context():
    duplicates = db.session.query(
        Article.url, db.func.count(Article.id)
    ).group_by(Article.url).having(db.func.count(Article.id) > 1).all()

    print("Doublons URL dans Article :")
    for url, count in duplicates:
        print(f"{url}: {count} fois")

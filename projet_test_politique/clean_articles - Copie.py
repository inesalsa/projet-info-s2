from models import db, Article, Question
from app import app  # <-- Assure-toi que ton app Flask est bien importée depuis le bon fichier

def clean_articles():
    print("Nettoyage des articles en doublon…")

    urls = db.session.query(Article.url).all()
    url_set = set()
    articles_to_delete = []

    for (url,) in urls:
        if url in url_set:
            article = Article.query.filter_by(url=url).first()
            if article:
                articles_to_delete.append(article)
        else:
            url_set.add(url)

    print(f"{len(articles_to_delete)} articles en double trouvés.")

    for article in articles_to_delete:
        # Supprime aussi les questions associées pour éviter les contraintes d'intégrité
        Question.query.filter_by(article_id=article.id).delete()
        db.session.delete(article)

    db.session.commit()
    print("Articles en double supprimés avec succès.")

# --- Le bloc magique ---
if __name__ == "__main__":
    with app.app_context():
        clean_articles()

# delete_data.py
from app import app
from models import Question, Reponse, db

def delete_all_data():
    with app.app_context():
        # Suppression des réponses d'abord (si elles dépendent des questions)
        Reponse.query.delete()
        Question.query.delete()
        db.session.commit()

        # Vérification
        print("✅ Suppression terminée.")
        print("Questions restantes :", Question.query.count())
        print("Réponses restantes :", Reponse.query.count())

# Lancer la suppression
delete_all_data()

from app import app, fetch_and_store_questions  # Assure-toi que fetch_and_store_questions est bien importé
from models import db, Question

# Test pour vérifier si les questions sont ajoutées correctement
def test_fetch_and_store():
    with app.app_context():  # Mets le contexte de l'application ici
        fetch_and_store_questions()  # Appeler la fonction qui récupère et stocke les questions
        questions = Question.query.all()  # Récupérer toutes les questions dans la base
        print(f"Questions trouvées : {len(questions)}")
        for question in questions:
            print(question.texte)

if __name__ == "__main__":
    test_fetch_and_store()

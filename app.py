import io
import os
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    send_file, flash
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, TableStyle, Paragraph, Spacer, LongTable

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gestion-notes-lycee-2026")

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or "sqlite:///gestion_notes_lycee.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# =================== MODELES ===================
class Annee(db.Model):
    __tablename__ = "annees"
    id = db.Column(db.Integer, primary_key=True)
    libelle = db.Column(db.String, nullable=False, unique=True)

class Classe(db.Model):
    __tablename__ = "classes"
    id = db.Column(db.Integer, primary_key=True)
    annee_id = db.Column(db.Integer, db.ForeignKey("annees.id", ondelete="CASCADE"), nullable=False)
    nom = db.Column(db.String, nullable=False)
    __table_args__ = (db.UniqueConstraint("annee_id", "nom"),)

class Etudiant(db.Model):
    __tablename__ = "etudiants"
    id = db.Column(db.Integer, primary_key=True)
    classe_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    prenom = db.Column(db.String, nullable=False)
    nom = db.Column(db.String, nullable=False)

class Discipline(db.Model):
    __tablename__ = "disciplines"
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String, nullable=False, unique=True)

class Coefficient(db.Model):
    __tablename__ = "coefficients"
    id = db.Column(db.Integer, primary_key=True)
    classe_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    discipline_id = db.Column(db.Integer, db.ForeignKey("disciplines.id", ondelete="CASCADE"), nullable=False)
    coef = db.Column(db.Float, nullable=False)
    __table_args__ = (db.UniqueConstraint("classe_id", "discipline_id"), db.CheckConstraint("coef > 0"))

class Trimestre(db.Model):
    __tablename__ = "trimestres"
    id = db.Column(db.Integer, primary_key=True)
    annee_id = db.Column(db.Integer, db.ForeignKey("annees.id", ondelete="CASCADE"), nullable=False)
    nom = db.Column(db.String, nullable=False)
    __table_args__ = (db.UniqueConstraint("annee_id", "nom"),)

class Evaluation(db.Model):
    __tablename__ = "evaluations"
    id = db.Column(db.Integer, primary_key=True)
    etudiant_id = db.Column(db.Integer, db.ForeignKey("etudiants.id", ondelete="CASCADE"), nullable=False)
    discipline_id = db.Column(db.Integer, db.ForeignKey("disciplines.id", ondelete="CASCADE"), nullable=False)
    trimestre_id = db.Column(db.Integer, db.ForeignKey("trimestres.id", ondelete="CASCADE"), nullable=False)
    type = db.Column(db.String, nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Float, nullable=False)
    __table_args__ = (
        db.UniqueConstraint("etudiant_id", "discipline_id", "trimestre_id", "numero"),
        db.CheckConstraint("type IN ('devoir', 'examen')"),
        db.CheckConstraint("numero BETWEEN 1 AND 4"),
        db.CheckConstraint("note BETWEEN 0 AND 20"),
    )

def initialiser_db():
    with app.app_context():
        db.create_all()

def page(contenu, **variables):
    return render_template_string(HTML_DEBUT + contenu + HTML_FIN, **variables)

def convertir_note(valeur):
    if valeur is None: return None
    texte = str(valeur).strip().replace(',', '.')
    if texte == '': return None
    try: note = float(texte)
    except ValueError: return None
    if note < 0 or note > 20: return None
    return note

def format_note(note): return '-' if note is None else f"{note:.2f}"

def moyenne_devoirs(etudiant_id, discipline_id, trimestre_id):
    return db.session.query(func.avg(Evaluation.note)).filter_by(
        etudiant_id=etudiant_id, discipline_id=discipline_id, trimestre_id=trimestre_id, type='devoir'
    ).scalar()

def note_examen(etudiant_id, discipline_id, trimestre_id):
    ligne = db.session.query(Evaluation.note).filter_by(
        etudiant_id=etudiant_id, discipline_id=discipline_id, trimestre_id=trimestre_id, type='examen', numero=4
    ).first()
    return None if ligne is None else ligne[0]

def statut_moyenne(moyenne):
    if moyenne is None: return 'Incomplet'
    return 'Ajourné' if moyenne < 12 else 'Validé'

def resultat_etudiant_discipline_trimestre(etudiant_id, discipline_id, trimestre_id):
    n_classe = moyenne_devoirs(etudiant_id, discipline_id, trimestre_id)
    n_exam = note_examen(etudiant_id, discipline_id, trimestre_id)
    moyenne = None
    if n_classe is not None and n_exam is not None:
        moyenne = (n_classe + 2 * n_exam) / 3
    return {'n_classe': n_classe, 'n_exam': n_exam, 'moyenne': moyenne, 'statut': statut_moyenne(moyenne)}

def coefficient_discipline_classe(classe_id, discipline_id):
    coef = db.session.query(Coefficient.coef).filter_by(classe_id=classe_id, discipline_id=discipline_id).scalar()
    return 1.0 if coef is None else coef

def disciplines_de_classe(classe_id):
    return db.session.query(Discipline.id, Discipline.nom, Coefficient.coef).join(
        Coefficient, Coefficient.discipline_id == Discipline.id
    ).filter(Coefficient.classe_id == classe_id).order_by(Discipline.nom).all()

def moyenne_finale_eleve_trimestre(etudiant_id, classe_id, trimestre_id):
    disciplines = disciplines_de_classe(classe_id)
    somme_moyennes_ponderees = 0.0
    somme_coefficients = 0.0
    for discipline in disciplines:
        resultat = resultat_etudiant_discipline_trimestre(etudiant_id, discipline.id, trimestre_id)
        if resultat['moyenne'] is None: continue
        coef = discipline.coef
        somme_moyennes_ponderees += resultat['moyenne'] * coef
        somme_coefficients += coef
    return None if somme_coefficients == 0 else somme_moyennes_ponderees / somme_coefficients

def compter(modele): return db.session.query(func.count()).select_from(modele).scalar()

HTML_DEBUT = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gestion des notes - Lycée</title><style>*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#172033;font-family:Arial,sans-serif;font-weight:bold}
header{background:#123b66;color:white;padding:15px}header h1{margin:0 0 12px;font-size:22px}nav{display:flex;flex-wrap:wrap;gap:7px}
nav a{color:white;text-decoration:none;border:1px solid white;border-radius:6px;padding:9px 10px;font-size:14px;text-align:center;overflow-wrap:anywhere}
main{width:100%;max-width:1300px;margin:auto;padding:15px}.card{background:white;border-radius:9px;padding:15px;margin-bottom:15px;box-shadow:0 2px 7px rgba(0,0,0,.08)}
h2,h3{margin-top:0}.info{background:#e5f1ff;border-left:5px solid #1764a0;padding:12px;margin:8px 0}.alert{background:#fff2c7;border-left:5px solid #bd7d00;padding:11px;margin-bottom:12px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;align-items:end}
label{display:block;margin-bottom:5px}input,select{width:100%;min-height:40px;border:1px solid #aeb8c4;border-radius:6px;padding:8px;font-size:15px;background:white;font-weight:bold}
button,.btn{display:inline-block;min-height:40px;max-width:100%;padding:9px 12px;border:1px solid #0d426f;border-radius:6px;background:#155a96;color:white;text-decoration:none;cursor:pointer;font-size:14px;text-align:center;overflow-wrap:anywhere;font-weight:bold}
button:hover,.btn:hover{background:#0c426f}.btn-success{background:#18794e;border-color:#12613e}.btn-warning{background:#a85d00;border-color:#814700}
.delete-cross{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:#c62828;color:white;text-decoration:none;font-size:22px;line-height:1;flex-shrink:0}
.delete-cross:hover{background:#8e1717}table{width:100%;margin-top:12px;border-collapse:collapse;background:white;table-layout:fixed}
th,td{border:1px solid #cbd4df;padding:9px 7px;text-align:left;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
th{background:#dce9f7}.factor-header{background:#f5f9fd;border:1px solid #cbd4df;border-radius:7px;padding:11px;margin-bottom:10px;text-align:center}
.factor-label{font-size:14px;color:#4f5d6c;margin-right:6px}.factor-value{font-size:16px;color:#123b66}.note-input{width:100%;min-height:42px;text-align:center;font-size:16px}
.evaluation-table{table-layout:fixed}@media (max-width:650px){main{padding:8px}nav a,button,.btn{flex:1 1 auto}th,td{padding:8px 5px;font-size:14px}}</style></head>
<body><header><h1>Gestion des notes - Lycée</h1><nav>
<a href="{{ url_for('accueil') }}">Accueil</a><a href="{{ url_for('annees') }}">Années</a><a href="{{ url_for('classes') }}">Classes</a>
<a href="{{ url_for('disciplines') }}">Disciplines</a><a href="{{ url_for('coefficients') }}">Coefficients</a>
<a href="{{ url_for('trimestres') }}">Trimestres</a><a href="{{ url_for('etudiants') }}">Étudiants</a>
<a href="{{ url_for('saisie') }}">Saisie</a><a href="{{ url_for('bulletin_pdf') }}">Bulletin PDF</a><a href="{{ url_for('pdf_annuel') }}">PDF annuel</a>
</nav></header><main>{% with messages = get_flashed_messages() %}{% if messages %}{% for message in messages %}<div class="alert">{{ message }}</div>{% endfor %}{% endif %}{% endwith %}"""

HTML_FIN = "</main></body></html>"

@app.route('/')
def accueil():
    stats = {'annees': compter(Annee),'classes': compter(Classe),'etudiants': compter(Etudiant),
             'disciplines': compter(Discipline),'trimestres': compter(Trimestre),'evaluations': compter(Evaluation)}
    contenu = """<div class="card"><h2>Tableau de bord</h2><div class="form-grid">
    <div class="info">Années : {{ stats.annees }}</div><div class="info">Classes : {{ stats.classes }}</div>
    <div class="info">Étudiants : {{ stats.etudiants }}</div><div class="info">Disciplines : {{ stats.disciplines }}</div>
    <div class="info">Trimestres : {{ stats.trimestres }}</div><div class="info">Évaluations : {{ stats.evaluations }}</div></div></div>"""
    return page(contenu, stats=stats)

@app.route('/annees', methods=['GET', 'POST'])
def annees():
    if request.method == 'POST':
        libelle = request.form.get('libelle', '').strip()
        if not libelle: flash('Veuillez saisir une année.')
        else:
            try: db.session.add(Annee(libelle=libelle)); db.session.commit(); flash('Année ajoutée.')
            except IntegrityError: db.session.rollback(); flash('Cette année existe déjà.')
        return redirect(url_for('annees'))
    liste = Annee.query.order_by(Annee.libelle.desc()).all()
    contenu = """<div class="card"><h2>Années scolaires</h2><form method="post" class="form-grid">
    <div><label>Année</label><input name="libelle" placeholder="2026-2027" required></div>
    <button class="btn btn-success">Ajouter</button></form></div>
    <div class="card"><h3>Liste des années</h3><table><tr><th>Année</th><th>Action</th></tr>
    {% for annee in liste %}<tr><td>{{ annee.libelle }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_annee', id=annee.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="2">Aucune année enregistrée.</td></tr>{% endfor %}</table></div>"""
    return page(contenu, liste=liste)

@app.route('/annees/supprimer/<int:id>')
def supprimer_annee(id):
    db.session.delete(db.get_or_404(Annee, id)); db.session.commit(); flash('Année supprimée.'); return redirect(url_for('annees'))

@app.route('/classes', methods=['GET', 'POST'])
def classes():
    annee_id = request.form.get('annee_id') if request.method == 'POST' else request.args.get('annee_id', '')
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        if not annee_id or not nom: flash('Veuillez choisir une année et saisir une classe.')
        else:
            try: db.session.add(Classe(annee_id=annee_id, nom=nom)); db.session.commit(); flash('Classe ajoutée.')
            except IntegrityError: db.session.rollback(); flash('Cette classe existe déjà dans cette année.')
        return redirect(url_for('classes', annee_id=annee_id))
    annees_liste = Annee.query.order_by(Annee.libelle.desc()).all()
    classes_par_annee = {}
    for c in db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom):
        if c.Annee.libelle not in classes_par_annee: classes_par_annee[c.Annee.libelle] = {'annee_id': c.Classe.annee_id, 'classes': []}
        classes_par_annee[c.Annee.libelle]['classes'].append({'id': c.Classe.id, 'nom': c.Classe.nom})
    contenu = """<div class="card"><h2>Classes</h2><form method="post" class="form-grid">
    <div><label>Année</label><select name="annee_id" required><option value="">Choisir</option>
    {% for annee in annees_liste %}<option value="{{ annee.id }}" {% if annee_id|string == annee.id|string %}selected{% endif %}>{{ annee.libelle }}</option>{% endfor %}</select></div>
    <div><label>Nom de la classe</label><input name="nom" placeholder="Classe A" required></div><button class="btn btn-success">Ajouter</button></form></div>
    {% for annee_libelle, donnees in classes_par_annee.items() %}<div class="card"><div class="factor-header">
    <span class="factor-label">Année :</span><span class="factor-value">{{ annee_libelle }}</span></div><table><tr><th>Classe</th><th>Action</th></tr>
    {% for classe in donnees.classes %}<tr><td>{{ classe.nom }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_classe', id=classe.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="2">Aucune classe dans cette année.</td></tr>{% endfor %}</table></div>
    {% else %}<div class="card"><div class="alert">Aucune classe enregistrée.</div></div>{% endfor %}"""
    return page(contenu, annees_liste=annees_liste, classes_par_annee=classes_par_annee, annee_id=annee_id)

@app.route('/classes/supprimer/<int:id>')
def supprimer_classe(id):
    db.session.delete(db.get_or_404(Classe, id)); db.session.commit(); flash('Classe supprimée.'); return redirect(url_for('classes'))

@app.route('/disciplines', methods=['GET', 'POST'])
def disciplines():
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        if not nom: flash('Veuillez saisir une discipline.')
        else:
            try: db.session.add(Discipline(nom=nom)); db.session.commit(); flash('Discipline ajoutée.')
            except IntegrityError: db.session.rollback(); flash('Cette discipline existe déjà.')
        return redirect(url_for('disciplines'))
    liste = Discipline.query.order_by(Discipline.nom).all()
    contenu = """<div class="card"><h2>Disciplines</h2><form method="post" class="form-grid">
    <div><label>Nom de la discipline</label><input name="nom" placeholder="Mathématiques" required></div>
    <button class="btn btn-success">Ajouter</button></form></div>
    <div class="card"><h3>Disciplines disponibles</h3><table><tr><th>Discipline</th><th>Action</th></tr>
    {% for discipline in liste %}<tr><td>{{ discipline.nom }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_discipline', id=discipline.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="2">Aucune discipline enregistrée.</td></tr>{% endfor %}</table></div>"""
    return page(contenu, liste=liste)

@app.route('/disciplines/supprimer/<int:id>')
def supprimer_discipline(id):
    db.session.delete(db.get_or_404(Discipline, id)); db.session.commit(); flash('Discipline supprimée.'); return redirect(url_for('disciplines'))

@app.route('/coefficients', methods=['GET', 'POST'])
def coefficients():
    classe_id = request.form.get('classe_id') if request.method == 'POST' else request.args.get('classe_id', '')
    discipline_id = request.form.get('discipline_id') if request.method == 'POST' else request.args.get('discipline_id', '')
    if request.method == 'POST':
        coef_val = convertir_note(request.form.get('coef'))
        if not classe_id or not discipline_id or coef_val is None: flash('Veuillez remplir tous les champs.')
        else:
            try: db.session.add(Coefficient(classe_id=classe_id, discipline_id=discipline_id, coef=coef_val)); db.session.commit(); flash('Coefficient enregistré.')
            except IntegrityError: db.session.rollback(); flash('Ce coefficient existe déjà.')
        return redirect(url_for('coefficients', classe_id=classe_id, discipline_id=discipline_id))
    classes_liste = db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom).all()
    disciplines_liste = Discipline.query.order_by(Discipline.nom).all()
    coefficients_liste = db.session.query(Coefficient, Classe, Annee, Discipline).join(Classe).join(Annee).join(Discipline).order_by(Annee.libelle.desc(), Classe.nom, Discipline.nom).all()
    contenu = """<div class="card"><h2>Coefficients</h2><form method="post" class="form-grid">
    <div><label>Classe</label><select name="classe_id" required><option value="">Choisir</option>
    {% for classe,annee in classes_liste %}<option value="{{ classe.id }}" {% if classe_id|string == classe.id|string %}selected{% endif %}>{{ annee.libelle }} - {{ classe.nom }}</option>{% endfor %}</select></div>
    <div><label>Discipline</label><select name="discipline_id" required><option value="">Choisir</option>
    {% for discipline in disciplines_liste %}<option value="{{ discipline.id }}" {% if discipline_id|string == discipline.id|string %}selected{% endif %}>{{ discipline.nom }}</option>{% endfor %}</select></div>
    <div><label>Coefficient</label><input name="coef" type="number" min="0.5" max="10" step="0.5" placeholder="2" required></div>
    <button class="btn btn-success">Enregistrer</button></form></div>
    <div class="card"><h3>Coefficients enregistrés</h3><table><tr><th>Année</th><th>Classe</th><th>Discipline</th><th>Coefficient</th><th>Action</th></tr>
    {% for coef,classe,annee,discipline in coefficients_liste %}<tr><td>{{ annee.libelle }}</td><td>{{ classe.nom }}</td><td>{{ discipline.nom }}</td>
    <td>{{ '%.1f'|format(coef.coef) }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_coefficient', id=coef.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="5">Aucun coefficient enregistré.</td></tr>{% endfor %}</table></div>"""
    return page(contenu, classes_liste=classes_liste, disciplines_liste=disciplines_liste, coefficients_liste=coefficients_liste, classe_id=classe_id, discipline_id=discipline_id)

@app.route('/coefficients/supprimer/<int:id>')
def supprimer_coefficient(id):
    db.session.delete(db.get_or_404(Coefficient, id)); db.session.commit(); flash('Coefficient supprimé.'); return redirect(url_for('coefficients'))

@app.route('/trimestres', methods=['GET', 'POST'])
def trimestres():
    annee_id = request.form.get('annee_id') if request.method == 'POST' else request.args.get('annee_id', '')
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        if not annee_id or not nom: flash('Veuillez choisir une année et saisir un trimestre.')
        else:
            try: db.session.add(Trimestre(annee_id=annee_id, nom=nom)); db.session.commit(); flash('Trimestre ajouté.')
            except IntegrityError: db.session.rollback(); flash('Ce trimestre existe déjà.')
        return redirect(url_for('trimestres', annee_id=annee_id))
    annees_liste = Annee.query.order_by(Annee.libelle.desc()).all()
    trimestres_par_annee = {}
    for t in db.session.query(Trimestre, Annee).join(Annee).order_by(Annee.libelle.desc(), Trimestre.nom):
        if t.Annee.libelle not in trimestres_par_annee: trimestres_par_annee[t.Annee.libelle] = {'annee_id': t.Trimestre.annee_id, 'trimestres': []}
        trimestres_par_annee[t.Annee.libelle]['trimestres'].append({'id': t.Trimestre.id, 'nom': t.Trimestre.nom})
    contenu = """<div class="card"><h2>Trimestres</h2><form method="post" class="form-grid">
    <div><label>Année</label><select name="annee_id" required><option value="">Choisir</option>
    {% for annee in annees_liste %}<option value="{{ annee.id }}" {% if annee_id|string == annee.id|string %}selected{% endif %}>{{ annee.libelle }}</option>{% endfor %}</select></div>
    <div><label>Nom du trimestre</label><input name="nom" placeholder="Trimestre 1" required></div><button class="btn btn-success">Ajouter</button></form></div>
    {% for annee_libelle, donnees in trimestres_par_annee.items() %}<div class="card"><div class="factor-header">
    <span class="factor-label">Année :</span><span class="factor-value">{{ annee_libelle }}</span></div><table><tr><th>Trimestre</th><th>Action</th></tr>
    {% for trimestre in donnees.trimestres %}<tr><td>{{ trimestre.nom }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_trimestre', id=trimestre.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="2">Aucun trimestre dans cette année.</td></tr>{% endfor %}</table></div>
    {% else %}<div class="card"><div class="alert">Aucun trimestre enregistré.</div></div>{% endfor %}"""
    return page(contenu, annees_liste=annees_liste, trimestres_par_annee=trimestres_par_annee, annee_id=annee_id)

@app.route('/trimestres/supprimer/<int:id>')
def supprimer_trimestre(id):
    db.session.delete(db.get_or_404(Trimestre, id)); db.session.commit(); flash('Trimestre supprimé.'); return redirect(url_for('trimestres'))

@app.route('/etudiants', methods=['GET', 'POST'])
def etudiants():
    classe_id = request.form.get('classe_id') if request.method == 'POST' else request.args.get('classe_id', '')
    if request.method == 'POST':
        prenom = request.form.get('prenom', '').strip(); nom = request.form.get('nom', '').strip()
        if not classe_id or not prenom or not nom: flash('Veuillez remplir tous les champs.')
        else: db.session.add(Etudiant(classe_id=classe_id, prenom=prenom, nom=nom)); db.session.commit(); flash('Étudiant ajouté.')
        return redirect(url_for('etudiants', classe_id=classe_id))
    classes_liste = db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom).all()
    etudiants_par_classe = {}
    for e in db.session.query(Etudiant, Classe, Annee).join(Classe).join(Annee).order_by(Annee.libelle.desc(), Classe.nom, Etudiant.nom, Etudiant.prenom):
        cle = f"{e.Annee.libelle} - {e.Classe.nom}"
        if cle not in etudiants_par_classe: etudiants_par_classe[cle] = []
        etudiants_par_classe[cle].append({'id': e.Etudiant.id, 'prenom': e.Etudiant.prenom, 'nom': e.Etudiant.nom})
    contenu = """<div class="card"><h2>Étudiants</h2><form method="post" class="form-grid">
    <div><label>Classe</label><select name="classe_id" required><option value="">Choisir</option>
    {% for classe,annee in classes_liste %}<option value="{{ classe.id }}" {% if classe_id|string == classe.id|string %}selected{% endif %}>{{ annee.libelle }} - {{ classe.nom }}</option>{% endfor %}</select></div>
    <div><label>Prénom</label><input name="prenom" placeholder="Moussa" required></div>
    <div><label>Nom</label><input name="nom" placeholder="DIALLO" required></div><button class="btn btn-success">Ajouter</button></form></div>
    {% for classe_cle, liste_etudiants in etudiants_par_classe.items() %}<div class="card"><div class="factor-header">
    <span class="factor-label">Classe :</span><span class="factor-value">{{ classe_cle }}</span></div><table><tr><th>Prénom</th><th>Nom</th><th>Action</th></tr>
    {% for e in liste_etudiants %}<tr><td>{{ e.prenom }}</td><td>{{ e.nom }}</td><td><a class="delete-cross" href="{{ url_for('supprimer_etudiant', id=e.id) }}" onclick="return confirm('Supprimer?')">×</a></td></tr>
    {% else %}<tr><td colspan="3">Aucun étudiant dans cette classe.</td></tr>{% endfor %}</table></div>
    {% else %}<div class="card"><div class="alert">Aucun étudiant enregistré.</div></div>{% endfor %}"""
    return page(contenu, classes_liste=classes_liste, etudiants_par_classe=etudiants_par_classe, classe_id=classe_id)

@app.route('/etudiants/supprimer/<int:id>')
def supprimer_etudiant(id):
    db.session.delete(db.get_or_404(Etudiant, id)); db.session.commit(); flash('Étudiant supprimé.'); return redirect(url_for('etudiants'))

@app.route('/saisie', methods=['GET', 'POST'])
def saisie():
    classes_liste = db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom).all()
    trimestres_liste = db.session.query(Trimestre, Annee).join(Annee).order_by(Annee.libelle.desc(), Trimestre.nom).all()
    disciplines_liste = Discipline.query.order_by(Discipline.nom).all()
    classe_id = request.args.get('classe_id', ''); trimestre_id = request.args.get('trimestre_id', '')
    discipline_id = request.args.get('discipline_id', ''); type_eval = request.args.get('type', 'devoir')
    classe = db.get(Classe, classe_id) if classe_id else None
    trimestre = db.get(Trimestre, trimestre_id) if trimestre_id else None
    discipline = db.get(Discipline, discipline_id) if discipline_id else None
    etudiants_liste = []; coef_discipline = 1.0
    if classe and trimestre and discipline:
        etudiants_liste = Etudiant.query.filter_by(classe_id=classe_id).order_by(Etudiant.nom, Etudiant.prenom).all()
        coef_discipline = coefficient_discipline_classe(classe_id, discipline_id)
        if request.method == 'POST':
            for etudiant in etudiants_liste:
                note = convertir_note(request.form.get(f'note_{etudiant.id}'))
                if note is None: continue
                numero = 1 if type_eval == 'devoir' else 4
                ev = Evaluation.query.filter_by(etudiant_id=etudiant.id, discipline_id=discipline_id, trimestre_id=trimestre_id, type=type_eval, numero=numero).first()
                if ev: ev.note = note
                else: db.session.add(Evaluation(etudiant_id=etudiant.id, discipline_id=discipline_id, trimestre_id=trimestre_id, type=type_eval, numero=numero, note=note))
            db.session.commit(); flash('Notes enregistrées.')
            return redirect(url_for('saisie', classe_id=classe_id, trimestre_id=trimestre_id, discipline_id=discipline_id, type=type_eval))
    contenu = """<div class="card"><h2>Saisie des notes</h2><form method="get" class="form-grid">
    <div><label>Classe</label><select name="classe_id" required><option value="">Choisir une classe</option>
    {% for c,a in classes_liste %}<option value="{{ c.id }}" {% if classe_id|string == c.id|string %}selected{% endif %}>{{ a.libelle }} - {{ c.nom }}</option>{% endfor %}</select></div>
    <div><label>Trimestre</label><select name="trimestre_id" required><option value="">Choisir un trimestre</option>
    {% for t,a in trimestres_liste %}<option value="{{ t.id }}" {% if trimestre_id|string == t.id|string %}selected{% endif %}>{{ a.libelle }} - {{ t.nom }}</option>{% endfor %}</select></div>
    <div><label>Discipline</label><select name="discipline_id" required><option value="">Choisir une discipline</option>
    {% for d in disciplines_liste %}<option value="{{ d.id }}" {% if discipline_id|string == d.id|string %}selected{% endif %}>{{ d.nom }}</option>{% endfor %}</select></div>
    <div><label>Type</label><select name="type" required><option value="devoir" {% if type_eval == 'devoir' %}selected{% endif %}>Devoir</option>
    <option value="examen" {% if type_eval == 'examen' %}selected{% endif %}>Examen</option></select></div><button class="btn" type="submit">Afficher</button></form></div>
    {% if classe and trimestre and discipline and etudiants_liste %}<div class="card"><div class="factor-header">
    <span class="factor-label">Année :</span><span class="factor-value">{{ classe.annee.libelle }}</span> | <span class="factor-label">Classe :</span><span class="factor-value">{{ classe.nom }}</span>
    | <span class="factor-label">Trimestre :</span><span class="factor-value">{{ trimestre.nom }}</span> | <span class="factor-label">Discipline :</span><span class="factor-value">{{ discipline.nom }}</span>
    | <span class="factor-label">Coeff :</span><span class="factor-value">{{ '%.1f'|format(coef_discipline) }}</span> | <span class="factor-label">Type :</span><span class="factor-value">{{ type_eval }}</span></div>
    <h3>Notes</h3><form method="post"><table class="evaluation-table"><tr><th>Étudiant</th><th>Note / 20</th></tr>
    {% for etudiant in etudiants_liste %}{% set note_existe = db.session.query(Evaluation.note).filter_by(etudiant_id=etudiant.id, discipline_id=discipline.id, trimestre_id=trimestre.id, type=type_eval, numero=1 if type_eval == 'devoir' else 4).scalar() %}
    <tr><td>{{ etudiant.prenom }} {{ etudiant.nom }}</td><td><input class="note-input" type="text" name="note_{{ etudiant.id }}" value="{{ note_existe if note_existe else '' }}" placeholder="0-20"></td></tr>{% endfor %}
    </table><div style="margin-top:12px;"><button class="btn btn-success" type="submit">Enregistrer toutes les notes</button></div></form></div>
    {% elif classe and trimestre and discipline %}<div class="card"><div class="alert">Aucun étudiant dans cette classe.</div></div>{% endif %}"""
    return page(contenu, classes_liste=classes_liste, trimestres_liste=trimestres_liste, disciplines_liste=disciplines_liste, classe_id=classe_id, trimestre_id=trimestre_id, discipline_id=discipline_id, type_eval=type_eval, classe=classe, trimestre=trimestre, discipline=discipline, etudiants_liste=etudiants_liste, coef_discipline=coef_discipline, db=db)

 
def creer_pdf(titre, sous_titre, donnees):
    sortie = io.BytesIO()
    doc = SimpleDocTemplate(
        sortie, pagesize=landscape(A4), rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    style_titre = ParagraphStyle('TitreCentre', parent=styles['Title'], alignment=TA_CENTER, fontSize=16, leading=20)
    elements = [Paragraph(titre, style_titre), Spacer(1, 8), Paragraph(sous_titre, styles['Normal']), Spacer(1, 12)]
    tableau = LongTable(donnees, colWidths=[140, 80, 80, 110, 90], repeatRows=1)
    tableau.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#174a78')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eef4fa')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d4edda')), ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(tableau); doc.build(elements); sortie.seek(0); return sortie

def creer_pdf_annuel(titre, sous_titre, donnees, nb_colonnes):
    sortie = io.BytesIO()
    doc = SimpleDocTemplate(sortie, pagesize=landscape(A4), rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    style_titre = ParagraphStyle('TitreCentre', parent=styles['Title'], alignment=TA_CENTER, fontSize=16, leading=20)
    elements = [Paragraph(titre, style_titre), Spacer(1, 8), Paragraph(sous_titre, styles['Normal']), Spacer(1, 12)]
    largeur_page = 520; largeur_premiere = 140; largeur_derniere = 110
    reste = largeur_page - largeur_premiere - largeur_derniere
    largeur_intermediaire = reste / max(nb_colonnes - 2, 1)
    col_widths = [largeur_premiere] + [largeur_intermediaire] * (nb_colonnes - 2) + [largeur_derniere]
    tableau = LongTable(donnees, colWidths=col_widths, repeatRows=1)
    tableau.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#174a78')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eef4fa')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d4edda')), ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(tableau); doc.build(elements); sortie.seek(0); return sortie

@app.route('/bulletin-pdf')
def bulletin_pdf():
    classes_liste = db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom).all()
    trimestres_liste = db.session.query(Trimestre, Annee).join(Annee).order_by(Annee.libelle.desc(), Trimestre.nom).all()
    classe_id = request.args.get('classe_id', ''); trimestre_id = request.args.get('trimestre_id', '')
    classe = db.get(Classe, classe_id) if classe_id else None
    trimestre = db.get(Trimestre, trimestre_id) if trimestre_id else None
    etudiants_liste = Etudiant.query.filter_by(classe_id=classe_id).order_by(Etudiant.nom, Etudiant.prenom).all() if classe else []
    contenu = """<div class="card"><h2>Bulletin PDF (par trimestre)</h2><div class="info">Choisissez une classe et un trimestre.</div>
    <form method="get" class="form-grid"><div><label>Classe</label><select name="classe_id" required><option value="">Choisir</option>
    {% for c,a in classes_liste %}<option value="{{ c.id }}" {% if classe_id|string == c.id|string %}selected{% endif %}>{{ a.libelle }} - {{ c.nom }}</option>{% endfor %}</select></div>
    <div><label>Trimestre</label><select name="trimestre_id" required><option value="">Choisir</option>
    {% for t,a in trimestres_liste %}<option value="{{ t.id }}" {% if trimestre_id|string == t.id|string %}selected{% endif %}>{{ a.libelle }} - {{ t.nom }}</option>{% endfor %}</select></div>
    <button class="btn" type="submit">Afficher la liste</button></form></div>
    {% if classe and trimestre %}<div class="card"><div class="factor-header">
    <span class="factor-label">Année :</span><span class="factor-value">{{ classe.annee.libelle }}</span> | 
    <span class="factor-label">Classe :</span><span class="factor-value">{{ classe.nom }}</span> | 
    <span class="factor-label">Trimestre :</span><span class="factor-value">{{ trimestre.nom }}</span></div>
    <table><tr><th>Élève</th><th>Trimestre</th><th>Action</th></tr>
    {% for etudiant in etudiants_liste %}<tr><td>{{ etudiant.prenom }} {{ etudiant.nom }}</td><td>{{ trimestre.nom }}</td>
    <td><a class="btn btn-success" href="{{ url_for('pdf_bulletin_eleve_trimestre', etudiant_id=etudiant.id, trimestre_id=trimestre.id) }}">PDF</a></td></tr>
    {% else %}<tr><td colspan="3">Aucun étudiant dans cette classe.</td></tr>{% endfor %}</table></div>{% endif %}"""
    return page(contenu, classes_liste=classes_liste, trimestres_liste=trimestres_liste, classe_id=classe_id, trimestre_id=trimestre_id, classe=classe, trimestre=trimestre, etudiants_liste=etudiants_liste)

@app.route('/pdf/bulletin-eleve-trimestre')
def pdf_bulletin_eleve_trimestre():
    etudiant_id = request.args.get('etudiant_id'); trimestre_id = request.args.get('trimestre_id')
    if not etudiant_id or not trimestre_id: return 'Étudiant ou trimestre manquant.', 400
    etudiant = db.get_or_404(Etudiant, etudiant_id); trimestre = db.get_or_404(Trimestre, trimestre_id)
    classe = db.get_or_404(Classe, etudiant.classe_id); annee = db.get_or_404(Annee, classe.annee_id)
    disciplines = disciplines_de_classe(etudiant.classe_id)
    moyenne_finale = moyenne_finale_eleve_trimestre(etudiant_id, etudiant.classe_id, trimestre_id)
    donnees = [['Discipline', 'Nclasse', 'NExam', 'Moy. générale', 'Statut']]
    for discipline in disciplines:
        resultat = resultat_etudiant_discipline_trimestre(etudiant_id, discipline.id, trimestre_id)
        donnees.append([discipline.nom, format_note(resultat['n_classe']), format_note(resultat['n_exam']), format_note(resultat['moyenne']), resultat['statut']])
    donnees.append(['MOYENNE FINALE', '', format_note(moyenne_finale), statut_moyenne(moyenne_finale) if moyenne_finale is not None else 'Incomplet'])
    pdf = creer_pdf("Bulletin de l'élève", f"Année : {annee.libelle} | Classe : {classe.nom} | Trimestre : {trimestre.nom} | Élève : {etudiant.prenom} {etudiant.nom}", donnees)
    return send_file(pdf, as_attachment=False, download_name='bulletin_eleve.pdf', mimetype='application/pdf')

@app.route('/pdf-annuel')
def pdf_annuel():
    classes_liste = db.session.query(Classe, Annee).join(Annee).order_by(Annee.libelle.desc(), Classe.nom).all()
    classe_id = request.args.get('classe_id', ''); classe = db.get(Classe, classe_id) if classe_id else None
    etudiants_liste = Etudiant.query.filter_by(classe_id=classe_id).order_by(Etudiant.nom, Etudiant.prenom).all() if classe else []
    contenu = """<div class="card"><h2>PDF annuel (par élève)</h2><div class="info">Choisissez une classe.</div>
    <form method="get" class="form-grid"><div><label>Classe</label><select name="classe_id" required><option value="">Choisir</option>
    {% for c,a in classes_liste %}<option value="{{ c.id }}" {% if classe_id|string == c.id|string %}selected{% endif %}>{{ a.libelle }} - {{ c.nom }}</option>{% endfor %}</select></div>
    <button class="btn" type="submit">Afficher la liste</button></form></div>
    {% if classe %}<div class="card"><div class="factor-header">
    <span class="factor-label">Année :</span><span class="factor-value">{{ classe.annee.libelle }}</span> | 
    <span class="factor-label">Classe :</span><span class="factor-value">{{ classe.nom }}</span></div>
    <table><tr><th>Élève</th><th>Action</th></tr>
    {% for etudiant in etudiants_liste %}<tr><td>{{ etudiant.prenom }} {{ etudiant.nom }}</td>
    <td><a class="btn btn-success" href="{{ url_for('pdf_annuel_eleve', etudiant_id=etudiant.id) }}">PDF</a></td></tr>
    {% else %}<tr><td colspan="2">Aucun étudiant dans cette classe.</td></tr>{% endfor %}</table></div>{% endif %}"""
    return page(contenu, classes_liste=classes_liste, classe_id=classe_id, classe=classe, etudiants_liste=etudiants_liste)

@app.route('/pdf/annuel-eleve')
def pdf_annuel_eleve():
    etudiant_id = request.args.get('etudiant_id')
    if not etudiant_id: return 'Étudiant manquant.', 400
    etudiant = db.get_or_404(Etudiant, etudiant_id)
    classe = db.get_or_404(Classe, etudiant.classe_id); annee = db.get_or_404(Annee, classe.annee_id)
    trimestres = Trimestre.query.filter_by(annee_id=classe.annee_id).order_by(Trimestre.nom).all()
    disciplines = disciplines_de_classe(etudiant.classe_id)
    en_tete = ['Discipline'] + [f"Moy. {t.nom}" for t in trimestres] + ['Moy. annuelle']
    donnees = [en_tete]
    for discipline in disciplines:
        ligne = [discipline.nom]; moyennes = []
        for trimestre in trimestres:
            resultat = resultat_etudiant_discipline_trimestre(etudiant_id, discipline.id, trimestre.id)
            moyennes.append(resultat['moyenne']); ligne.append(format_note(resultat['moyenne']))
        vals = [m for m in moyennes if m is not None]
        moyenne_annuelle = None if not vals else sum(vals) / len(vals); ligne.append(format_note(moyenne_annuelle)); donnees.append(ligne)
    total = 0.0; n = 0
    for trimestre in trimestres:
        m = moyenne_finale_eleve_trimestre(etudiant_id, etudiant.classe_id, trimestre.id)
        if m is not None: total += m; n += 1
    if n > 0: donnees.append(['MOYENNE FINALE ANNUELLE'] + ['' for _ in trimestres] + [format_note(total / n)])
    pdf = creer_pdf_annuel('Relevé annuel', f"Année : {annee.libelle} | Classe : {classe.nom} | Élève : {etudiant.prenom} {etudiant.nom}", donnees, len(trimestres) + 2)
    return send_file(pdf, as_attachment=False, download_name='releve_annuel_eleve.pdf', mimetype='application/pdf')

if __name__ == '__main__':
    initialiser_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)    
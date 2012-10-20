import datetime

from flask import request, redirect, url_for, render_template, Blueprint, flash
from peewee import *
from wtfpeewee.orm import model_form

from flask_peewee.utils import get_object_or_404, object_list, slugify

from app import db
from auth import auth

def create_tables():
    Article.create_table(fail_silently=True)

class Article(db.Model):
    title = CharField()
    slug = CharField() # URL-friendly name
    content = TextField()
    status = IntegerField(choices=((1, 'draft'),(2, 'revision'), (3, 'published')), default=1)
    #category = IntegerField(choices=((1, 'world'), (2, 'person'), (3, 'rule')))
    created_date = DateTimeField(default=datetime.datetime.now)
    modified_date = DateTimeField()
    style = CharField(null=True) # URI to stylesheet to activate when displaying
    image = CharField(null=True) # URI to an image-ID that shows up as icon, thumbnail, etc
    
    # For self-referring keys, we need this line as the object self is not created
    # when creating this. See http://peewee.readthedocs.org/en/latest/peewee/fields.html#self-referential-foreign-keys
    parent = ForeignKeyField('self', related_name='children', null=True)

    class Meta:
        order_by = ('modified_date',)

    def __unicode__(self):
        return self.title

    def save(self, ):
        self.slug = slugify(self.title)
        self.modified_date = datetime.datetime.now()
        return super(Article, self).save()
        
class Metadata(db.Model): # Metadata to any article
    article = ForeignKeyField(Article)
    key = CharField()
    value = CharField()

world = Blueprint('world', __name__, template_folder='templates')

@world.route('/')
@auth.login_required
def index():
    print "Index"
    qr = Article.select()
    return object_list('world/index.html', qr)


@world.route('/<title>/', methods=['GET', 'POST'])
@auth.login_required
def detail(title):
    WikiForm = model_form(Article, only=('title', 'content',))

    try:
        article = Article.get(title=title)
    except Article.DoesNotExist:
        article = Article(title=title)

    if request.method == 'POST':
        form = WikiForm(request.form, obj=article)
        if form.validate():
            form.populate_obj(article)
            article.save()
            flash('Your changes have been saved')
            return redirect(url_for('world.detail', title=article.title))
        else:
            flash('There were errors with your submission')
    else:
        form = WikiForm(obj=article)

    return render_template('world/detail.html', article=article, form=form)


@world.route('/<title>/delete/', methods=['GET', 'POST'])
@auth.login_required
def delete(title):
    article = get_object_or_404(Article, title=title)
    if request.method == 'POST':
        article.delete_instance()
        return redirect(url_for('world.index'))

    return render_template('world/delete.html', article=article)


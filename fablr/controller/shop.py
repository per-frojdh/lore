"""
  controller.shop
  ~~~~~~~~~~~~~~~~

  This is the controller and Flask blueprint for a basic webshopg. It will
  setup the URL routes based on Resource and provide a checkout flow. It
  also hosts important return URLs for the payment processor.

  :copyright: (c) 2014 by Helmgast AB
"""
import logging
from datetime import datetime
from itertools import izip

import stripe
from flask import Blueprint, current_app, g, request, url_for, redirect, abort, session, flash, Markup
from flask_babel import lazy_gettext as _
from flask_classy import route
from flask_mongoengine.wtf import model_form
from mongoengine import NotUniqueError, ValidationError, Q
from wtforms.fields import FormField, FieldList, StringField
from wtforms.fields.html5 import EmailField, IntegerField
from wtforms.utils import unset_value
from wtforms.validators import InputRequired, Email, DataRequired, NumberRange

from fablr.controller.mailer import send_mail
from fablr.controller.resource import (ResourceAccessPolicy,
                                       RacModelConverter, RacBaseForm, ResourceView,
                                       filterable_fields_parser, prefillable_fields_parser, ListResponse, ItemResponse,
                                       Authorization, route_subdomain)
from fablr.controller.world import set_theme
from fablr.model.asset import FileAsset
from fablr.model.misc import EMPTY_ID
from fablr.model.shop import Product, Order, OrderLine, Address, OrderStatus, Stock, ProductStatus
from fablr.model.user import User
from fablr.model.world import Publisher

logger = current_app.logger if current_app else logging.getLogger(__name__)

shop_app = Blueprint('shop', __name__, template_folder='../templates/shop')

stripe.api_key = current_app.config['STRIPE_SECRET_KEY']


def get_or_create_stock(publisher):
    stock = Stock.objects(publisher=publisher).first()
    if not stock:
        stock = Stock(publisher=publisher)
        stock.save()
    return stock


def filter_product_published():
    return Q(status__ne=ProductStatus.hidden, created__lte=datetime.utcnow())


def filter_order_authorized():
    if not g.user:
        return Q(id=EMPTY_ID)
    return Q(user=g.user)


def filter_authorized_by_publisher(publisher=None):
    if not g.user:
        return Q(id=EMPTY_ID)
    if not publisher:
        # Check all publishers
        return Q(publisher__in=Publisher.objects(Q(editors__all=[g.user]) | Q(readers__all=[g.user])))
    elif g.user in publisher.editors or g.user in publisher.readers:
        # Save some time and only check given publisher
        return Q(publisher__in=[publisher])
    else:
        return Q(id=EMPTY_ID)


class ProductAccessPolicy(ResourceAccessPolicy):
    def is_editor(self, op, user, res):
        if res.publisher and user in res.publisher.editors:
            return Authorization(True, _("Allowed access to %(op)s %(res)s as editor", op=op, res=res),
                                 privileged=True)
        else:
            return Authorization(False, _("Not allowed access to %(op)s %(res)s as not an editor", op=op, res=res))

    def is_reader(self, op, user, res):
        if res.publisher and user in res.publisher.readers:
            return Authorization(True, _("Allowed access to %(op)s %(res)s as reader", op=op, res=res),
                                 privileged=True)
        else:
            return Authorization(False, _("Not allowed access to %(op)s %(res)s as not a reader", op=op, res=res))

    def is_resource_public(self, op, res):
        return Authorization(True, _("Public resource")) if res.status != 'hidden' else \
            Authorization(False, _("Not a public resource"))


class ProductsView(ResourceView):
    subdomain = '<pub_host>'
    access_policy = ProductAccessPolicy()
    model = Product
    list_template = 'shop/product_list.html'
    list_arg_parser = filterable_fields_parser(['title', 'description', 'created', 'type', 'world', 'price'])
    item_template = 'shop/product_item.html'
    item_arg_parser = prefillable_fields_parser(['title', 'description', 'created', 'type', 'world', 'price'])
    form_class = model_form(Product,
                            base_class=RacBaseForm,
                            exclude=['slug'],
                            converter=RacModelConverter())
    # Add stock count as a faux input field of the ProductForm
    form_class.stock_count = IntegerField(label=_("Remaining Stock"), validators=[DataRequired(), NumberRange(min=-1)])

    def index(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        products = Product.objects().order_by('type', '-price')
        r = ListResponse(ProductsView, [('products', products), ('publisher', publisher)])
        if not (g.user and g.user.admin):
            r.query = r.query.filter(
                filter_product_published() |
                filter_authorized_by_publisher(publisher))
        r.auth_or_abort(res=publisher)
        r.prepare_query()
        set_theme(r, 'publisher', publisher.slug)

        return r

    def get(self, id):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        if id == 'post':
            r = ItemResponse(ProductsView, [('product', None), ('publisher', publisher)], extra_args={'intent': 'post'})
            r.auth_or_abort(res=publisher)
        else:
            product = Product.objects(slug=id).first_or_404()
            # We will load the stock count from the publisher specific Stock object
            stock = get_or_create_stock(publisher)
            stock_count = stock.stock_count.get(product.slug, None)
            extra_form_args = {} if stock_count is None else {'stock_count': stock_count}
            r = ItemResponse(ProductsView, [('product', product), ('publisher', publisher)],
                             extra_form_args=extra_form_args)
            r.stock = stock
            r.auth_or_abort()
        set_theme(r, 'publisher', publisher.slug)

        return r

    def post(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        r = ItemResponse(ProductsView, [('product', None), ('publisher', publisher)], method='post')
        r.auth_or_abort(res=publisher)
        r.stock = get_or_create_stock(publisher)
        product = Product()
        set_theme(r, 'publisher', publisher.slug)
        if not r.validate():
            return r, 400  # Respond with same page, including errors highlighted
        r.form.populate_obj(product)
        try:
            r.commit(new_instance=product)
        except (NotUniqueError, ValidationError) as err:
            return r.error_response(err)
        r.stock.stock_count[product.slug] = r.form.stock_count.data
        r.stock.save()
        return redirect(r.args['next'] or url_for('shop.ProductsView:get', id=product.slug))

    def patch(self, id):

        # fa = []
        # for i in request.form.getlist('images'):
        #     fa.append(FileAsset.objects(id=i).first())
        # print fa

        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        product = Product.objects(slug=id).first_or_404()
        r = ItemResponse(ProductsView, [('product', product), ('publisher', publisher)], method='patch')
        r.auth_or_abort()
        r.stock = get_or_create_stock(publisher)
        set_theme(r, 'publisher', publisher.slug)

        if not r.validate():
            return r, 400  # Respond with same page, including errors highlighted
        r.form.populate_obj(product, request.form.keys())  # only populate selected keys
        try:
            r.commit()
            r.stock.stock_count[product.slug] = r.form.stock_count.data
            r.stock.save()
        except (NotUniqueError, ValidationError) as err:
            return r.error_response(err)
        return redirect(r.args['next'] or url_for('shop.ProductsView:get', id=product.slug))

    def delete(self, id):
        abort(503)  # Unsafe to delete products as they are referred to in orders
        # publisher = Publisher.objects(slug=publisher).first_or_404()
        # product = Product.objects(slug=id).first_or_404()
        # r = ItemResponse(ProductsView, [('product', product), ('publisher', publisher)], method='delete')
        # r.auth_or_abort()
        # set_theme(r, 'publisher', publisher.slug)
        # r.commit()
        # stock = Stock.objects(publisher=publisher).first()
        # if stock:
        #     del stock.stock_count[product.slug]
        #     stock.save()
        # return redirect(r.args['next'] or url_for('shop.ProductsView:index', pub_host=publisher.slug))


ProductsView.register_with_access(shop_app, 'product')


@shop_app.route('/', subdomain='<pub_host>')
def shop_home():
    if ProductsView.access_policy.authorize(op='list'):
        return redirect(url_for('shop.ProductsView:index'))
    else:
        return redirect(url_for('shop.OrdersView:my_orders'))

# shop_app.add_url_rule('/', endpoint='shop_home', subdomain='<publisher>', redirect_to='/shop/products/')

CartOrderLineForm = model_form(OrderLine, only=['quantity'], base_class=RacBaseForm, converter=RacModelConverter())
# Orderlines that only include comments, to allow for editing comments but not the order lines as such
LimitedOrderLineForm = model_form(OrderLine, only=['comment'], base_class=RacBaseForm, converter=RacModelConverter())
AddressForm = model_form(Address, base_class=RacBaseForm, converter=RacModelConverter())


class FixedFieldList(FieldList):
    # TODO
    # Below is a very hacky approach to handle updating the order_list. When we send in a form
    # with a deleted row, it never appears in formdata. For example, we have a order_list of 2 items,
    # when the first is deleted only the second is submitted. Below code uses the indices of the
    # field ids, e.g. order_lines-0 and order_lines-1 to identify what was removed, and then
    # process and populate the right item from the OrderList field of the model.
    # This should be fixed by wtforms!

    def process(self, formdata, data=unset_value):
        print 'FieldList process formdata %s, data %s' % (formdata, data)
        self.entries = []
        if data is unset_value or not data:
            try:
                data = self.default()
            except TypeError:
                data = self.default

        self.object_data = data

        if formdata:
            indices = sorted(set(self._extract_indices(self.name, formdata)))
            if self.max_entries:
                indices = indices[:self.max_entries]

            for index in indices:
                try:
                    obj_data = data[index]
                    print "Got obj_data %s" % obj_data
                except LookupError:
                    obj_data = unset_value
                self._add_entry(formdata, obj_data, index=index)
                # if not indices:  # empty the list
                #     self.entries = []
        else:
            for obj_data in data:
                self._add_entry(formdata, obj_data)

        while len(self.entries) < self.min_entries:
            self._add_entry(formdata)

    def populate_obj(self, obj, name):
        old_values = getattr(obj, name, [])

        candidates = []
        indices = [e.id.rsplit('-', 1)[1] for e in self.entries]
        for i in indices:
            candidates.append(old_values[int(i)])

        _fake = type(str('_fake'), (object,), {})
        output = []
        for field, data in izip(self.entries, candidates):
            fake_obj = _fake()
            fake_obj.data = data
            field.populate_obj(fake_obj, 'data')
            output.append(fake_obj.data)

        setattr(obj, name, output)


class BuyForm(RacBaseForm):
    product = StringField(validators=[InputRequired(_("Please enter your email address."))])


class CartForm(RacBaseForm):
    order_lines = FixedFieldList(FormField(CartOrderLineForm))


class DetailsForm(RacBaseForm):
    shipping_address = FormField(AddressForm)
    email = EmailField("Email", validators=[
        InputRequired(_("Please enter your email address.")),
        Email(_("Please enter your email address."))])


class PaymentForm(RacBaseForm):
    order_lines = FixedFieldList(FormField(LimitedOrderLineForm))
    stripe_token = StringField(validators=[InputRequired(_("Error, missing Stripe token"))])


class PostPaymentForm(RacBaseForm):
    order_lines = FixedFieldList(FormField(LimitedOrderLineForm))


class OrdersAccessPolicy(ResourceAccessPolicy):
    def is_editor(self, op, user, res):
        return Authorization(False, _("No editor access to orders"))

    def is_reader(self, op, user, res):
        if user and user == res.user:
            return Authorization(True, _("Allowed access to %(op)s %(res)s as owner of order", op=op, res=res),
                                 privileged=True)
        else:
            return Authorization(False, _("Not allowed access to %(op)s %(res)s as not owner of order", op=op, res=res))


class OrdersView(ResourceView):
    subdomain = '<pub_host>'
    access_policy = OrdersAccessPolicy()
    model = Order
    list_template = 'shop/order_list.html'
    list_arg_parser = filterable_fields_parser(
        ['id', 'user', 'created', 'updated', 'status', 'total_price', 'total_items'])
    item_template = 'shop/order_item.html'
    item_arg_parser = prefillable_fields_parser(
        ['id', 'user', 'created', 'updated', 'status', 'total_price', 'total_items'])
    form_class = form_class = model_form(Order,
                                         base_class=RacBaseForm,
                                         only=['order_lines', 'shipping_address', 'shipping_mobile'],
                                         converter=RacModelConverter())

    def index(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        orders = Order.objects().order_by('-updated')  # last updated will show paid highest

        r = ListResponse(OrdersView, [('orders', orders), ('publisher', publisher)])

        r.auth_or_abort(res=publisher)
        if not (g.user and g.user.admin):
            r.query = r.query.filter(filter_order_authorized())
        r.prepare_query()
        aggregate = list(r.orders.aggregate({'$group':
            {
                '_id': None,
                'total_value': {'$sum': '$total_price'},
                'min_created': {'$min': '$created'},
                'max_created': {'$max': '$created'}
            }
        }))
        r.aggregate = aggregate[0] if aggregate else None

        set_theme(r, 'publisher', publisher.slug)
        return r

    def my_orders(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        orders = Order.objects(user=g.user).order_by('-updated')  # last updated will show paid highest
        r = ListResponse(OrdersView, [('orders', orders), ('publisher', publisher)], method='my_orders')
        r.auth_or_abort(res=publisher)
        r.prepare_query()
        set_theme(r, 'publisher', publisher.slug)
        return r

    def get(self, id):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        # TODO we dont support new order creation outside of cart yet
        # if id == 'post':
        #     r = ItemResponse(OrdersView, [('order', None), ('publisher', publisher)], extra_args={'intent': 'post'})
        order = Order.objects(id=id).first_or_404()
        r = ItemResponse(OrdersView, [('order', order), ('publisher', publisher)], form_class=PostPaymentForm)
        r.auth_or_abort()
        set_theme(r, 'publisher', publisher.slug)
        return r

    def patch(self, id, publisher):
        abort(501)  # Not implemented

    @route('/buy', methods=['PATCH'])
    def buy(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        cart_order = get_cart_order()
        r = ItemResponse(OrdersView, [('order', cart_order), ('publisher', publisher)], form_class=BuyForm,
                         method='patch')
        if not r.validate():
            return r, 400  # Respond with same page, including errors highlighted
        p = Product.objects(slug=r.form.product.data).first()
        if p:
            if not cart_order:
                # Create new cart-order and attach to session
                cart_order = Order(status='cart')  # status defaults to cart, but let's be explicit
                if g.user:
                    cart_order.user = g.user
                cart_order.save()  # Need to save to get an id
                session['cart_id'] = str(cart_order.id)
                r.instance = cart_order  # set it in the response as well
            found = False
            for ol in cart_order.order_lines:
                if ol.product == p:
                    ol.quantity += 1
                    found = True
            if not found:  # create new orderline with this product
                new_ol = OrderLine(product=p, price=p.price)
                cart_order.order_lines.append(new_ol)
            try:
                cart_order.save()
            except (NotUniqueError, ValidationError) as err:
                return r.error_response(err)
            return r
        abort(400, 'Badly formed cart patch request')

    # Post means go to next step, patch means to stay
    @route('/cart', methods=['GET', 'PATCH', 'POST'])
    def cart(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        cart_order = get_cart_order()
        r = ItemResponse(OrdersView, [('order', cart_order), ('publisher', publisher)], form_class=CartForm,
                         extra_args={'view': 'cart', 'intent': 'post'})
        set_theme(r, 'publisher', publisher.slug)
        if request.method in ['PATCH', 'POST']:
            r.method = request.method.lower()
            if not r.validate():
                return r, 400  # Respond with same page, including errors highlighted
            r.form.populate_obj(cart_order)  # populate all of the object
            try:
                r.commit(flash=False)
            except (NotUniqueError, ValidationError) as err:
                return r.error_response(err)
            if request.method == 'PATCH':
                return redirect(r.args['next'] or url_for('shop.OrdersView:cart', **request.view_args))
            elif request.method == 'POST':
                return redirect(r.args['next'] or url_for('shop.OrdersView:details', **request.view_args))
        return r  # we got here if it's a get

    @route('/details', methods=['GET', 'POST'])
    def details(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        cart_order = get_cart_order()
        if not cart_order or cart_order.total_items < 1:
            return redirect(url_for('shop.OrdersView:cart'))

        r = ItemResponse(OrdersView, [('order', cart_order), ('publisher', publisher)], form_class=DetailsForm,
                         extra_args={'view': 'details', 'intent': 'post'})
        set_theme(r, 'publisher', publisher.slug)
        if request.method == 'POST':
            r.method = 'post'
            if not r.validate():
                return r, 400  # Respond with same page, including errors highlighted
            r.form.populate_obj(cart_order)  # populate all of the object
            if not g.user and User.objects(email=cart_order.email)[:1]:
                # An existing user has this email, force login or different email
                flash(Markup(_(
                    'Email belongs to existing user, please <a href="%(loginurl)s">login</a> first or change email',
                    loginurl=url_for('auth.login', next=request.url))),
                    'danger')
                return r, 400
            if not cart_order.is_digital():
                shipping_products = Product.objects(
                    publisher=publisher,
                    type='shipping',
                    currency=cart_order.currency,
                    description__contains=cart_order.shipping_address.country).order_by('-price')
                if shipping_products:
                    cart_order.shipping = shipping_products[0]
            try:
                r.commit(flash=False)
            except (NotUniqueError, ValidationError) as err:
                return r.error_response(err)
            return redirect(r.args['next'] or url_for('shop.OrdersView:pay', **request.view_args))
        return r  # we got here if it's a get

    @route('/pay', methods=['GET', 'POST'])
    def pay(self):
        publisher = Publisher.objects(slug=g.pub_host).first_or_404()
        cart_order = get_cart_order()
        if not cart_order or not cart_order.shipping_address or not cart_order.user:
            return redirect(url_for('shop.OrdersView:cart'))
        r = ItemResponse(OrdersView, [('order', cart_order), ('publisher', publisher)], form_class=PaymentForm,
                         extra_args={'view': 'pay', 'intent': 'post'})
        set_theme(r, 'publisher', publisher.slug)
        r.stripe_key = current_app.config['STRIPE_PUBLIC_KEY']
        if request.method == 'POST':
            r.method = 'post'
            if not r.validate():
                return r, 400  # Respond with same page, including errors highlighted
            r.form.populate_obj(cart_order)  # populate all of the object
            # Remove the purchased quantities from the products, ensuring we don't go below zero
            # If failed, the product has no more stock, we have to abort purchase
            stock_available = cart_order.deduct_stock(publisher)
            if not stock_available:
                r.errors = [('danger', 'A product in this order is out of stock, purchase cancelled')]
                return r, 400
            try:
                # Will raise CardError if not succeeded
                charge = stripe.Charge.create(
                    source=r.form.stripe_token.data,
                    amount=cart_order.total_price_int(),  # Stripe takes input in "cents" or similar
                    currency=cart_order.currency,
                    description=unicode(cart_order),
                    metadata={'order_id': cart_order.id}
                )
                cart_order.status = OrderStatus.paid
                cart_order.charge_id = charge['id']

                r.commit()
                g.user.log(action='purchase', resource=cart_order, metric=cart_order.total_price_sek())
                send_mail(recipients=[g.user.email], message_subject=_('Thank you for your order!'), mail_type='order',
                          cc=[current_app.config['MAIL_DEFAULT_SENDER']], user=g.user, order=cart_order,
                          publisher=publisher)
            except stripe.error.CardError as ce:
                r.errors = [('danger', ce.json_body['error']['message'])]
                return r, 400
            except ValidationError as ve:
                r.errors = [('danger', ve._format_errors())]
                return r, 400  # Respond with same page, including errors highlighted
            finally:
                # Executes at any exception from above try clause, before returning / raising
                # Return purchased quantities to the products
                cart_order.return_stock(publisher)
            return redirect(r.args['next'] or url_for('shop.OrdersView:get', id=cart_order.id, **request.view_args))
        return r  # we got here if it's a get


OrdersView.register_with_access(shop_app, 'order')


def get_cart_order():
    if session.get('cart_id', None):
        # We have a cart in the session
        cart_order = Order.objects(id=session['cart_id']).first()
        if not cart_order or cart_order.status != 'cart' or (cart_order.user and cart_order.user != g.user):
            # Error, maybe someone is manipulating input, or we logged out and should clear the
            # association with that cart for safety
            # True if current user is different, or if current user is none, and cart_order.user is not
            session.pop('cart_id')
            return None
        elif not cart_order.user and g.user:
            # We have logged in and cart in session lacks a user, means the cart came from before login
            # There may be old carts registered to this user, let's delete them, e.g. overwrite with new cart
            Order.objects(status='cart', user=g.user).delete()
            # Attach cart from session to the newly logged in user
            cart_order.user = g.user
            cart_order.save()  # Save the new cart
        return cart_order
    elif g.user:
        # We have no cart_id in session yet, but we have a user, so someone has just logged in
        # Let's find any old carts belonging to this user
        cart_order = Order.objects(user=g.user, status='cart').first()
        if cart_order:
            session['cart_id'] = cart_order.id
        return cart_order
    else:
        return None


# This injects the "cart_items" into templates in shop_app
@shop_app.context_processor
def inject_cart():
    cart_order = get_cart_order()
    return dict(cart_items=cart_order.total_items if cart_order else 0)

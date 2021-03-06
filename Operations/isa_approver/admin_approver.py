from osv import fields, osv
from tools.translate import _
import StringIO
import cStringIO
import base64
import string
from openerp import tools
import datetime
from datetime import datetime, timedelta
from email_template import email_template
import openerp.addons.decimal_precision as dp

class mail_compose_message(osv.TransientModel):
    _inherit = "mail.compose.message"
    _name = "mail.compose.message"
    
    
    def generate_email_for_composer(self, cr, uid, template_id, res_id, context=None):
        """ Call email_template.generate_email(), get fields relevant for
            mail.compose.message, transform email_cc and email_to into partner_ids """
        template_values = self.pool.get('email.template').generate_email(cr, uid, template_id, res_id, context=context)
        # filter template values
        fields = ['body_html', 'subject', 'email_to', 'email_recipients', 'email_cc', 'attachment_ids', 'attachments']
        
        values = dict((field, template_values[field]) for field in fields if template_values.get(field))

        values['body'] = values.pop('body_html', '')
        # transform email_to, email_cc into partner_ids
        partner_ids = set()
        mails = tools.email_split(values.pop('email_to', '') + ' ' + values.pop('email_cc', ''))
        ctx = dict((k, v) for k, v in (context or {}).items() if not k.startswith('default_'))
        for mail in mails:

            partner_id = self.pool.get('res.partner').find_or_create(cr, uid, mail, context=ctx)
            partner_ids.add(partner_id)
        email_recipients = values.pop('email_recipients', '')
        if email_recipients:
            for partner_id in email_recipients.split(','):
                if partner_id:  # placeholders could generate '', 3, 2 due to some empty field values
                    partner_ids.add(int(partner_id))
        # legacy template behavior: void values do not erase existing values and the
        # related key is removed from the values dict
        if partner_ids:
            values['partner_ids'] = list(partner_ids)

        return values       
    
    
    
    def send_mail(self, cr, uid, ids, context=None):
        """ Process the wizard content and proceed with sending the related
            email(s), rendering any template patterns on the fly if needed. """
        if context is None:
            context = {}
        ir_attachment_obj = self.pool.get('ir.attachment')
        active_ids = context.get('active_ids')
        
        is_log = context.get('mail_compose_log', False)
        for wizard in self.browse(cr, uid, ids, context=context):
            mass_mail_mode = wizard.composition_mode == 'mass_mail'
            active_model_pool_name = wizard.model if wizard.model else 'mail.thread'
            active_model_pool = self.pool.get(active_model_pool_name)
            # wizard works in batch mode: [res_id] or active_ids
            
            res_ids = active_ids if mass_mail_mode and wizard.model and active_ids else [wizard.res_id]
            for res_id in res_ids:
                
                # mail.message values, according to the wizard options
                post_values = {
                    'subject': wizard.subject,
                    'body': wizard.body,
                    'parent_id': wizard.parent_id and wizard.parent_id.id,
                    'partner_ids': [partner.id for partner in wizard.partner_ids],
                    

                    
                    'attachment_ids': [attach.id for attach in wizard.attachment_ids],
                }
                
                if mass_mail_mode and wizard.model:
                    email_dict = self.render_message(cr, uid, wizard, res_id, context=context)
                    post_values['partner_ids'] += email_dict.pop('partner_ids', [])
                    post_values['attachments'] = email_dict.pop('attachments', [])
                    

                    
                    attachment_ids = []
                    for attach_id in post_values.pop('attachment_ids'):
                        new_attach_id = ir_attachment_obj.copy(cr, uid, attach_id, {'res_model': self._name, 'res_id': wizard.id}, context=context)
                        attachment_ids.append(new_attach_id)
                    post_values['attachment_ids'] = attachment_ids
                    post_values.update(email_dict)
                
                subtype = 'mail.mt_comment'
                if is_log:  # log a note: subtype is False
                    subtype = False
                elif mass_mail_mode:  # mass mail: is a log pushed to recipients, author not added
                    subtype = False
                    context = dict(context, mail_create_nosubscribe=True)  # add context key to avoid subscribing the author
                msg_id = active_model_pool.message_post(cr, uid, [res_id], type='comment', subtype=subtype, context=context, **post_values)
                # mass_mailing: notify specific partners, because subtype was False, and no-one was notified
                if mass_mail_mode and post_values['partner_ids']:
                    self.pool.get('mail.notification')._notify(cr, uid, msg_id, post_values['partner_ids'], context=context)
                self.pool.get('project.project').write(cr,uid,active_ids,{'state_for_appr': 'sent_for_approval'})
        return {'type': 'ir.actions.act_window_close'}

    

class project_project(osv.osv):
    _inherit = "project.project"
    
    def _data_get_kick(self, cr, uid, ids, name, arg, context=None):
        if context is None:
            context = {}
        result = {}
        location = self.pool.get('ir.config_parameter').get_param(cr, uid, 'ir_attachment.location')
        bin_size = context.get('bin_size')
        for attach in self.browse(cr, uid, ids, context=context):
            if location and attach.store_fname:
                result[attach.id] = self._file_read(cr, uid, location, attach.store_fname, bin_size)
            else:
                result[attach.id] = attach.db_datas
        return result
    
    def _data_set_kick(self, cr, uid, id, name, value, arg, context=None):
        # We dont handle setting data to null
        if not value:
            return True
        if context is None:
            context = {}
        location = self.pool.get('ir.config_parameter').get_param(cr, uid, 'ir_attachment.location')
        file_size = len(value.decode('base64'))
        if location:
            attach = self.browse(cr, uid, id, context=context)
            if attach.store_fname:
                self._file_delete(cr, uid, location, attach.store_fname)
            fname = self._file_write(cr, uid, location, value)
            super(project_project, self).write(cr, uid, [id], {'store_fname': fname, 'file_size': file_size}, context=context)
        else:
            super(project_project, self).write(cr, uid, [id], {'db_datas': value, 'file_size': file_size}, context=context)
        return True
    
    def _data_get_document(self, cr, uid, ids, name, arg, context=None):
        if context is None:
            context = {}
        result = {}
        location = self.pool.get('ir.config_parameter').get_param(cr, uid, 'ir_attachment.location')
        bin_size = context.get('bin_size')
        for attach in self.browse(cr, uid, ids, context=context):
            if location and attach.datas_project_charter:
                result[attach.id] = self._file_read(cr, uid, location, attach.datas_project_charter, bin_size)
            else:
                result[attach.id] = attach.db_datas1
        return result
    
    def _data_set_document(self, cr, uid, id, name, value, arg, context=None):
        # We dont handle setting data to null
        if not value:
            return True
        if context is None:
            context = {}
        location = self.pool.get('ir.config_parameter').get_param(cr, uid, 'ir_attachment.location')
        file_size = len(value.decode('base64'))
        if location:
            attach = self.browse(cr, uid, id, context=context)
            if attach.store_fname_doc:
                self._file_delete(cr, uid, location, attach.store_fname_doc)
            fname = self._file_write(cr, uid, location, value)
            super(project_project, self).write(cr, uid, [id], {'store_fname_doc': fname, 'file_size1': file_size}, context=context)
        else:
            super(project_project, self).write(cr, uid, [id], {'db_datas1': value, 'file_size1': file_size}, context=context)
        return True
    
    def send_mail_approve(self, cr, uid, ids, context=None):
            test=[]
            email_template_obj = self.pool.get('email.template')
            ir_model_data = self.pool.get('ir.model.data')
            template_ids = email_template_obj.search(cr, uid, [('model_id.model', '=','project.project')], context=context)
            if template_ids:
                values = email_template_obj.generate_email(cr, uid, template_ids[0], ids, context=context)
                email_obj=self.browse(cr,uid,ids[0])
                try:
                    compose_form_id = ir_model_data.get_object_reference(cr, uid, 'mail', 'email_compose_message_wizard_form')[1]
                except ValueError:
                    compose_form_id = False
                
                values['res_id'] = False
                mail_mail_obj = self.pool.get('mail.mail')
                msg_id = mail_mail_obj.create(cr, uid, values, context=context)
                if msg_id:
                    mail_mail_obj.send(cr, uid, [msg_id], context=context)
                ctx = dict(context)

                ctx['default_template_id'] = template_ids[0]
                return {
                        'type': 'ir.actions.act_window',
                        'view_type': 'form',
                        'view_mode': 'form',
                        'res_model': 'mail.compose.message',
                        'views': [(compose_form_id, 'form')],
                        'view_id': compose_form_id,
                        'target': 'new',
                        'context': ctx,
                        }

    
    def approve_by_admin(self,cr,uid,ids,context=None):
        if not ids:
            ids=self.search(cr,uid,ids)
        for certi_obj in self.browse(cr,uid, ids,context=None):
            if not certi_obj.datas and not certi_obj.approve_id.id:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Approver and Upload Presentation'))
            elif not certi_obj.approve_id.id:
                raise osv.except_osv(
                    _('Error!'),
                    _('You cannot send mail until select Approver'))
            elif not certi_obj.datas:
                raise osv.except_osv(
                    _('Error!'),
                    _('You cannot send mail until select Upload Presentation'))
            if certi_obj.approve_id.id and certi_obj.datas:
                self.write(cr, uid, ids, {'state_for_appr': 'approval'})
        return True
        
    def send_mail_approve_doc(self,cr,uid, ids,context=None):
        for certi_obj in self.browse(cr,uid, ids,context=None):
            if not certi_obj.datas_project_charter and not certi_obj.approve_project_charter_id.id :
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Approver and Upload Project Document'))
            elif not certi_obj.datas_project_charter:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Upload Project Document'))
            elif not certi_obj.approve_project_charter_id.id:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Approver'))
            if certi_obj.approve_project_charter_id.id and certi_obj.datas_project_charter:
                test_email=self.pool.get('res.users').browse(cr,uid,certi_obj.approve_project_charter_id.id).email
                mail_mail = self.pool.get('mail.mail')
                asunto = 'Product Document Validation'
                body = '' 
                body += '<br/>'
                body = 'Hello sir' 
                body += '<br/>' 
                body += 'Need to approve your validation' 
                body += '<br/>' 
                body += 'Thank you'
                mail_ids = []
                mail_ids.append(mail_mail.create(cr, uid, {
                                'email_from': "drishtitechtestmail@gmail.com",
                                'email_to': test_email,
                                'subject': asunto,
                                'body_html': '<pre>%s</pre>' % body}, context=context))
                mail_mail.send(cr, uid, mail_ids, context=context)
            self.write(cr, uid, ids, {'state_for_appr_project': 'sent_for_approval'})
        return True
    
    def approve_by_pro_admin(self,cr,uid,ids,context=None):
        if not ids:
            ids=self.search(cr,uid,ids)
        for certi_obj in self.browse(cr,uid, ids,context=None):
            if not certi_obj.datas_project_charter and not certi_obj.approve_project_charter_id.id:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Approver and Upload Project Document'))
            elif not certi_obj.approve_project_charter_id.id:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Approver'))
                
            elif not certi_obj.datas_project_charter:
                    raise osv.except_osv(
                        _('Error!'),
                        _('You cannot send mail until select Upload Project Document'))
                
            if certi_obj.approve_project_charter_id.id and certi_obj.datas_project_charter:
                self.write(cr, uid, ids, {'state_for_appr_project': 'approval'})
        return True
    
    _columns = {
        'datas_fname': fields.char('File Name',size=256),
        'datas': fields.function(_data_get_kick, fnct_inv=_data_set_kick, string='Upload Presentation', type="binary", nodrop=True),
        'approve_id':fields.many2one('res.users','Select Approver'),
        'comment_datas':fields.text('Remarks'),
        'store_fname': fields.char('Stored Filename', size=256),
        'datas_project_charter_fname': fields.char('File Name',size=256),
        'datas_project_charter': fields.function(_data_get_document, fnct_inv=_data_set_document, string='Upload Project Document', type="binary", nodrop=True),
        'approve_project_charter_id':fields.many2one('res.users','Select Approver'),
        'comment_project':fields.text('Remarks'),
        'datas_finance_document_fname': fields.char('File Name',size=256),
        'datas_finance_document': fields.function(_data_get_document, fnct_inv=_data_set_document, string='Upload Finance Document', type="binary", nodrop=True),
        'approve_finance_document_id':fields.many2one('res.users','Select Approver'),
        'comment_finance':fields.text('Remarks'),
        'db_datas': fields.binary('Database Data'),
        'db_datas1': fields.binary('Database Data'),
        'store_fname_doc': fields.char('Stored Filename', size=256),
        'file_size': fields.integer('File Size'),
        'file_size1': fields.integer('File Size'),
        'state': fields.selection([('template', 'Template'),
                                   ('draft','New'),
                                   ('initiate','Initiate'),
                                   ('planning', 'Planning'),
                                   ('open','Execution'), 
                                   ('cancelled', 'Cancelled'),
                                   ('pending','Pending'),('close','Closed')], 'Status', required=True,),
        'state_for_appr': fields.selection([ 
                                   ('new','New'),
                                   ('sent_for_approval', 'Sent For Approval'),
                                   ('approval', 'Approved'),],'Status',readonly=True,),
        'state_for_appr_project': fields.selection([
                                   ('new','New'),
                                   ('sent_for_approval', 'Sent For Approval'),
                                   ('approval', 'Approved'),],'Status',readonly=True,)
    }
    _defaults={
              'state':'draft',
    }
project_project()
    

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from flask import Flask
import pyotp
from services import chart_accounts, account_security

class AdministrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=Flask(__name__)
        self.app.config.update(TESTING=True,ACCOUNT_DATABASE=str(Path(self.temp.name)/'accounts.sqlite'))
        self.app.register_blueprint(chart_accounts.blueprint)
        self.app.add_url_rule('/api/test-tactic','test_tactic',lambda: account_security.protect_tactic_request() or {'ok':True},methods=['GET','POST'])
        self.password='testing-long-password'
        self.client=self.app.test_client()
        self.csrf=self.client.get('/api/account/session').get_json()['csrf']
        with self.app.app_context(),chart_accounts.database() as db:
            salt='01'*32
            hashed=chart_accounts.password_hash(self.password,salt)
            db.execute("INSERT INTO users(id,name,salt,hash,role,email_verified) VALUES (1,'owner',?,?,'admin',1)",(salt,hashed))
            db.execute("INSERT INTO users(id,name,salt,hash,role,email_verified) VALUES (2,'member',?,?,'member',1)",(salt,hashed))
        self.login()

    def tearDown(self): self.temp.cleanup()
    def call(self,path,body=None,method='POST'):
        response=self.client.open('/api/account/'+path,method=method,json=body or {},headers={'X-CSRF-Token':self.csrf})
        if response.get_json().get('csrf'): self.csrf=response.get_json()['csrf']
        return response
    def login(self,name='owner',code=''):
        return self.call('login',{'username':name,'password':self.password,'twoFactorCode':code})
    def credentials(self,**extra): return dict(currentPassword=self.password,**extra)

    def test_role_changes_reauth_self_protection_and_audit(self):
        self.assertEqual(self.client.get('/api/account/admin/users').status_code,200)
        self.assertEqual(self.call('admin/users/2',{'role':'tactics'},'PUT').status_code,403)
        self.assertEqual(self.call('admin/users/2',self.credentials(role='tactics'),'PUT').status_code,200)
        self.assertEqual(self.call('admin/users/1',self.credentials(role='member'),'PUT').status_code,409)
        self.assertEqual(self.call('admin/users/1',self.credentials(),'DELETE').status_code,409)
        self.assertEqual(self.call('admin/users/2',self.credentials(),'DELETE').status_code,200)
        self.assertEqual(len(self.client.get('/api/account/admin/users').get_json()['audit']),2)

    def test_member_cannot_manage_users(self):
        self.call('logout');self.login('member')
        self.assertEqual(self.client.get('/api/account/session').get_json()['user']['role'],'member')
        self.assertEqual(self.client.get('/api/account/admin/users').status_code,403)
        self.assertEqual(self.call('admin/users/1',self.credentials(role='admin'),'PUT').status_code,403)

    def test_tactic_endpoint_role_and_csrf(self):
        self.assertEqual(self.client.get('/api/test-tactic').status_code,200)
        self.assertEqual(self.client.post('/api/test-tactic').status_code,403)
        self.assertEqual(self.client.post('/api/test-tactic',headers={'X-CSRF-Token':self.csrf}).status_code,200)
        self.call('logout');self.login('member')
        self.assertEqual(self.client.get('/api/test-tactic').status_code,403)

    def test_donations_official_links_and_iban(self):
        response=self.call('donation',self.credentials(settings={'kofi':'https://evil.example/donate'}),'PUT')
        self.assertEqual(response.status_code,400)
        settings={'kofi':'https://ko-fi.com/test','stripe':'https://buy.stripe.com/test','revolut':'https://revolut.me/test','iban':'DE89370400440532013000','beneficiary':'Test Owner'}
        self.assertEqual(self.call('donation',self.credentials(settings=settings),'PUT').status_code,200)
        self.assertEqual(self.client.get('/api/account/donation').get_json()['settings']['iban'],settings['iban'])
        settings['iban']='DE00370400440532013000'
        self.assertEqual(self.call('donation',self.credentials(settings=settings),'PUT').status_code,400)

    def test_mfa_encrypted_confirmation_replay_and_recovery(self):
        setup=self.call('mfa/setup',self.credentials()).get_json()
        secret=setup['secret'];now=int(time.time()//30)*30
        with patch('services.account_security.time.time',return_value=now):
            result=self.call('mfa/confirm',{'twoFactorCode':pyotp.TOTP(secret).at(now)})
        self.assertEqual(result.status_code,200)
        recovery=result.get_json()['recoveryCodes'][0]
        with self.app.app_context(),chart_accounts.database() as db:
            row=db.execute('SELECT * FROM users WHERE id=1').fetchone()
            self.assertNotIn(secret,row['totp_secret']);self.assertNotIn(recovery,row['recovery'])
        self.call('logout')
        self.assertEqual(self.login().status_code,401)
        with patch('services.account_security.time.time',return_value=now+30):
            code=pyotp.TOTP(secret).at(now+30)
            self.assertEqual(self.login(code=code).status_code,200)
            self.call('logout')
            self.assertEqual(self.login(code=code).status_code,401)
        self.assertEqual(self.login(code=recovery).status_code,200)
        self.call('logout')
        self.assertEqual(self.login(code=recovery).status_code,401)

if __name__=='__main__': unittest.main()

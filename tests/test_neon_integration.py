"""Opt-in real PostgreSQL smoke test, isolated in a disposable schema."""
import json
import os
from pathlib import Path
import time
import unittest
import uuid
from unittest.mock import patch

from flask import Flask
from services import chart_accounts, account_security, chart_alerts, storage


@unittest.skipUnless(os.environ.get('ZGUA_TEST_POSTGRES_URL'), 'Explicit development test URL required')
class NeonIntegrationTests(unittest.TestCase):
    def test_accounts_documents_alerts_and_mail_ledger(self):
        import psycopg2
        from services import mail_store
        url = os.environ['ZGUA_TEST_POSTGRES_URL']
        schema = 'zgua_test_' + uuid.uuid4().hex
        setup = psycopg2.connect(url, connect_timeout=15)
        setup.autocommit = True
        setup.cursor().execute('CREATE SCHEMA ' + schema)

        class ScopedPool:
            def getconn(self):
                connection = psycopg2.connect(url, connect_timeout=15)
                connection.cursor().execute('SET LOCAL search_path TO ' + schema)
                return connection

            def putconn(self, connection, close=False):
                connection.close()

        try:
            with patch.object(storage, '_pool', return_value=ScopedPool()):
                with storage.postgres_database(url) as db:
                    for migration in sorted((Path(__file__).resolve().parents[1]/'migrations').glob('*.sql')):
                        db.execute(migration.read_text(encoding='utf-8'))
                    salt = '02'*32
                    hashed = chart_accounts.password_hash('integration-long-password',salt)
                    for identity in (1,2):
                        db.execute('INSERT INTO users(id,name,salt,hash,email,email_verified,role) VALUES (?,?,?,?,?,1,?)',
                                   (identity,'integration'+str(identity),salt,hashed,'test'+str(identity)+'@example.org','admin' if identity==1 else 'member'))
                app = Flask(__name__)
                app.config.update(TESTING=True,DATABASE_URL=url)
                app.register_blueprint(chart_accounts.blueprint)
                app.register_blueprint(chart_alerts.blueprint)
                first, second = app.test_client(), app.test_client()
                for client, identity in ((first,1),(second,2)):
                    token = client.get('/api/account/session').get_json()['csrf']
                    result = client.post('/api/account/login',json={'username':'integration'+str(identity),'password':'integration-long-password'},headers={'X-CSRF-Token':token})
                    self.assertEqual(result.status_code,200,result.get_json())
                csrf = first.get('/api/account/session').get_json()['csrf']
                body = {'profile':{'zgua-custom-indicators-v1':json.dumps([{'id':'custom_test','name':'Test','code':'x=1'}]),'zgua-ui-preferences':json.dumps({'watchLists':[{'id':'yellow','name':'Yellow','symbols':['BTCUSDT']}],'fontSize':12})},'revision':0}
                self.assertEqual(first.put('/api/account/profile',json=body,headers={'X-CSRF-Token':csrf}).status_code,200)
                self.assertEqual(first.put('/api/account/profile',json=body,headers={'X-CSRF-Token':csrf}).status_code,409)
                restored = first.get('/api/account/session').get_json()
                self.assertEqual(json.loads(restored['profile']['zgua-custom-indicators-v1'])[0]['code'],'x=1')
                self.assertIsNone(second.get('/api/account/session').get_json()['profile'])
                self.assertEqual(first.get('/api/account/admin/users').status_code,200)
                self.assertEqual(second.get('/api/account/admin/users').status_code,403)
                from services import backtest_store
                cookie = first.get_cookie(chart_accounts.COOKIE).value if hasattr(first,'get_cookie') else next(cookie.value for cookie in first.cookie_jar if cookie.name == chart_accounts.COOKIE)
                with app.test_request_context(headers={'Cookie':chart_accounts.COOKIE+'='+cookie}):
                    backtest_store.record({'id':'isolated-run','totalReturnPct':1}, {'tradeList':[{'entryPrice':100,'exitPrice':101}]})
                    self.assertNotIn('tradeList',backtest_store.history()[0])
                self.assertEqual(first.get('/api/account/backtests/isolated-run').get_json()['tradeList'][0]['exitPrice'],101)
                self.assertEqual(second.get('/api/account/backtests/isolated-run').status_code,404)
                alert = {'source':'bybit','symbol':'BTCUSDT','timeframe':'1h','conditions':[{'field':'price','operator':'above','value':100}], 'notifications':{'email':False},'expiresAt':time.time()+86400}
                with patch.object(chart_alerts,'market_values',return_value={'price':{'value':90}}):
                    created = first.post('/api/chart-alerts',json=alert,headers={'X-CSRF-Token':csrf})
                    self.assertEqual(created.status_code,201,created.get_json())
                    self.assertEqual(second.get('/api/chart-alerts').get_json()['alerts'],[])
                with app.app_context():
                    self.assertIsNone(mail_store.claim('test/mail','test@example.org','Integration',{'daily_limit':10,'monthly_limit':10}))
                    mail_store.finish('test/mail',result={'provider':'resend','id':'test-provider-id','status':'accepted'})
                    self.assertEqual(mail_store.claim('test/mail','test@example.org','Integration',{})['id'],'test-provider-id')
        finally:
            setup.cursor().execute('DROP SCHEMA ' + schema + ' CASCADE')
            setup.close()


if __name__ == '__main__':
    unittest.main()

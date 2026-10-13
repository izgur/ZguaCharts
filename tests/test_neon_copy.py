"""Opt-in transfer checks using two disposable schemas in development only."""
import os
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from services import storage
from scripts.copy_neon_database import transfer


@unittest.skipUnless(os.environ.get('ZGUA_TEST_POSTGRES_URL'),'Explicit development test URL required')
class NeonCopyTests(unittest.TestCase):
    def test_copy_preserves_current_data_and_refuses_overwrite(self):
        import psycopg2
        url = os.environ['ZGUA_TEST_POSTGRES_URL']
        names = ['zgua_copy_'+uuid.uuid4().hex for _ in range(2)]
        setup = psycopg2.connect(url,connect_timeout=15)
        setup.autocommit = True
        for name in names:
            setup.cursor().execute('CREATE SCHEMA '+name)

        class Pool:
            def __init__(self,schema):
                self.schema = schema

            def getconn(self):
                connection = psycopg2.connect(url,connect_timeout=15)
                connection.cursor().execute('SET search_path TO '+self.schema)
                connection.commit()
                return connection

            def putconn(self,connection,close=False):
                connection.close()

        source_url = 'postgresql://source.example/db?sslmode=require'
        target_url = 'postgresql://target.example/db?sslmode=require'
        try:
            with patch.object(storage,'_pool',side_effect=lambda value:Pool(names[0] if value==source_url else names[1])):
                for destination in (source_url,target_url):
                    with storage.postgres_database(destination) as db:
                        db.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TIMESTAMPTZ DEFAULT now())')
                        for path in sorted((Path(__file__).resolve().parents[1]/'migrations').glob('*.sql')):
                            db.execute(path.read_text(encoding='utf-8'))
                            db.execute('INSERT INTO schema_migrations(version) VALUES (?)',(path.name,))
                with storage.postgres_database(source_url) as db:
                    db.execute("INSERT INTO users(id,name,salt,hash,email,role) VALUES(1,'copy-test','test-salt','test-hash','copy@example.test','admin')")
                    db.execute('UPDATE account_sequence SET next_id=9')
                    db.execute("INSERT INTO profiles VALUES(1,'{}',3)")
                    db.execute("INSERT INTO user_documents VALUES(1,'preferences','font','{}',3,1)")
                    db.execute("INSERT INTO user_document_versions VALUES(1,'tactic','example',1,'{}',1)")
                    db.execute("INSERT INTO sessions VALUES('must-not-copy',1,'test-csrf',9999999999)")
                    db.execute("INSERT INTO admin_audit(actor,action,created) VALUES(1,'fixture',1)")
                    db.execute('INSERT INTO alert_records VALUES (?,?,?,?,?)',('copied-alert',1,json.dumps({'enabled':True}),1,1))
                counts = transfer(source_url,target_url,None)
                self.assertEqual(counts['users'],1)
                self.assertEqual(counts['user_document_versions'],1)
                with storage.postgres_database(target_url) as db:
                    self.assertEqual(db.execute('SELECT next_id FROM account_sequence').fetchone()[0],9)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT revision FROM profiles').fetchone()[0],3)
                    alert = json.loads(db.execute("SELECT body FROM alert_records WHERE id='copied-alert'").fetchone()[0])
                    self.assertFalse(alert['enabled'])
                    self.assertTrue(alert['migrationSourceEnabled'])
                    db.execute("INSERT INTO admin_audit(actor,action,created) VALUES(1,'after-copy',2)")
                with self.assertRaises(ValueError):
                    transfer(source_url,target_url,None)
                with storage.postgres_database(target_url) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
                with self.assertRaises(ValueError):
                    transfer(source_url,source_url,None)
        finally:
            for name in names:
                setup.cursor().execute('DROP SCHEMA '+name+' CASCADE')
            setup.close()


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from server import create_app
from tag_completion import TagIndex,suggestions

class Tags(unittest.TestCase):
    def test_csv_aliases_quality_tags_and_prefix_ranking(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);main=root/'tags.csv';extra=root/'extra-quality-tags.csv'
            main.write_text('long_hair,0,300,"longhair,hair_long"\nlong_sleeves,0,200,\nartist_(name),1,5,"artistname"\nmasterpiece,5,12,\n',encoding='utf-8')
            extra.write_text('masterpiece,5,Quality tag,,\nbest_quality,5,Quality tag,,\n',encoding='utf-8')
            index=TagIndex([main,extra])
            self.assertEqual(index.search('long h')[0][0],'long_hair')
            self.assertEqual(index.search('longhair')[0][3],'longhair')
            self.assertEqual(index.search('hair_long')[0][0],'long_hair')
            self.assertEqual(index.search('sleeve')[0][0],'long_sleeves')
            self.assertEqual(index.search('artistname')[0][1],1)
            self.assertEqual(index.search('best q')[0][0],'best_quality')
            self.assertEqual(index.search('masterpiece')[0][2],12)
            self.assertEqual(len([r for r in index.rows if r['tag']=='masterpiece']),1)
            self.assertEqual(index.search('x'),());self.assertEqual(index.search('a'*81),())

    def test_real_dictionary_and_read_only_api(self):
        result=suggestions('fuji choko');self.assertGreater(result['total_tags'],100000)
        self.assertEqual(result['items'][0]['tag'],'fuzichoco');self.assertEqual(result['items'][0]['category'],1)
        self.assertEqual(suggestions('masterpiece')['items'][0]['tag'],'masterpiece')
        self.assertEqual(suggestions('x'),{'items':[]})
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(Path(folder),False)
            try:
                with TestClient(app) as client:
                    r=client.get('/api/tag-completions',params={'q':'long h','limit':5});self.assertEqual(r.status_code,200)
                    self.assertEqual(r.json()['items'][0]['tag'],'long_hair');self.assertLessEqual(len(r.json()['items']),5)
                    self.assertLessEqual(len(client.get('/api/tag-completions',params={'q':'ha','limit':1000}).json()['items']),20)
                    self.assertEqual(client.get('/api/jobs').json(),[])
                    self.assertEqual(client.get('/api/tag-completions',params={'q':'a'*1000}).json(),{'items':[]})
            finally:app.state.store.db.close()

if __name__=='__main__':unittest.main()

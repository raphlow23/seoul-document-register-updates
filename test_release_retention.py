import unittest
from unittest.mock import Mock,patch
from release_retention import plan,prune


def release(number,identity,draft=False,complete=True):
    names=["latest.json",f"SeoulDocumentRegister-{number}.exe",f"SeoulDocumentRegister-Setup-{number}.zip"] if complete else []
    return {"tag_name":"v"+number,"id":identity,"draft":draft,"assets":[{"id":identity*10+i,"name":n,"state":"uploaded","size":100} for i,n in enumerate(names)]}


class ReleaseRetentionTests(unittest.TestCase):
    def test_keeps_latest_and_previous_numerically(self):
        releases=[release("2.9.0",1),release("2.10.0",2),release("2.8.0",3),release("2.0.0",4)]
        keep,remove=plan(releases,releases[1])
        self.assertEqual([r["tag_name"] for r in keep],["v2.10.0","v2.9.0"])
        self.assertCountEqual([r["id"] for r in remove],[3,4])

    def test_failed_or_unpublished_current_never_deletes(self):
        for current in (release("2.3.0",1,draft=True),release("2.3.0",1,complete=False)):
            with self.assertRaises(ValueError):plan([release("2.2.0",2)],current)

    def test_never_deletes_newer_drafts_or_unknown_named_releases(self):
        current=release("2.3.0",1)
        releases=[current,release("2.2.0",2),release("2.1.0",3),release("2.4.0",4,draft=True),{"tag_name":"manual-backup","id":5}]
        keep,remove=plan(releases,current)
        self.assertEqual([r["id"] for r in remove],[3])

    def test_no_cleanup_without_a_healthy_rollback_version(self):
        current=release("2.3.0",1)
        keep,remove=plan([release("2.2.0",2,complete=False)],current)
        self.assertEqual(keep,[current]);self.assertEqual(remove,[])

    def test_api_deletes_only_obsolete_assets_and_release(self):
        old,previous,current=release("2.1.0",1),release("2.2.0",2),release("2.3.0",3)
        inventory=Mock();inventory.json.return_value=[current,previous,old]
        check=Mock();check.json.return_value=old
        with patch("release_retention.requests.get",side_effect=[inventory,check]),patch("release_retention.requests.delete") as delete:
            result=prune("https://api.github.com/repos/example/project",{},current)
        urls=[call.args[0] for call in delete.call_args_list]
        self.assertEqual(urls,["https://api.github.com/repos/example/project/releases/assets/10","https://api.github.com/repos/example/project/releases/assets/11","https://api.github.com/repos/example/project/releases/assets/12","https://api.github.com/repos/example/project/releases/1"])
        self.assertEqual(result["asset_bytes_removed"],300)
        self.assertEqual(result["kept"],["v2.3.0","v2.2.0"])

    def test_changed_identity_stops_before_deleting(self):
        old,previous,current=release("2.1.0",1),release("2.2.0",2),release("2.3.0",3)
        inventory=Mock();inventory.json.return_value=[current,previous,old]
        changed=Mock();changed.json.return_value=previous
        with patch("release_retention.requests.get",side_effect=[inventory,changed]),patch("release_retention.requests.delete") as delete:
            with self.assertRaises(ValueError):prune("https://api.github.com/repos/example/project",{},current)
        delete.assert_not_called()


if __name__=="__main__":unittest.main()

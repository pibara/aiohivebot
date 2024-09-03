#!/usr/bin/env python3
"""Simple demo script that looks for votes on already paid out posts"""
import asyncio
import json
from dateutil.parser import parse
import operator
from aiohivebot import BaseBot

VERSION = "0.2.0"
AUTHORS = {}
AUTHORS["hive-archology"] = ["pibara", "croupierbot"]
AUTHORS["aiohivebot"] = ["pibara", "pibara"]

def make_body(author, benef, curation_rewards):
    """Construct the proxy comment"""
    ben2prod = {}
    for key, val in AUTHORS.items():
        ben2prod[val[1]] = key
    rval = """This is a [hive-archeology](https://github.com/pibara/hive-archeology) proxy comment meant as a proxy for
upvoting good content that is past it's initial pay-out window.

![image.png](https://files.peakd.com/file/peakd-hive/pibara/EppQ5vutzcx8r5YZ2LiXjW7EnMtgkcam6KpByUfiojAYYXLBmKFTC1jom5Kig1H9Z1w.png)

<sub><sup>Pay-out for this comment is configured as followed:</sup></sub>

| <sub><sup>role</sup></sub> | <sub><sup>account</sup></sub> | <sub><sup>percentage</sup></sub> | <sub><sup>note</sup></sub>|
| --- | --- | --- | --- |
"""
    if curation_rewards:
        divider = 200
        rval += "| <sub><sup>curator</sup></sub> | <sub><sup>-</sup></sub> | <sub><sup>50.0%</sup></sub> | <sub><sup>curation rewards enabled</sup></sub> |"
    else:
        divider = 100
        rval += "| <sub><sup>curator</sup></sub> | <sub><sup>-</sup></sub> | <sub><sup>0.0%</sup></sub> | <sub><sup>curation rewards disabled</sup></sub> |\n"
    for beneficiary in benef:
        share = beneficiary.get("weight",0) / divider
        if beneficiary.get("account","") == author:
            rval += "| <sub><sup>author</sup></sub> | <sub><sup>@"
            rval += author
            rval += "</sup></sub> | <sub><sup>"
            rval += str(share)
            rval += "%</sup></sub> | <sub><sup></sup></sub> |\n"
        elif beneficiary.get("account","") in ben2prod:
            prod = ben2prod[beneficiary["account"]]
            rval += "| <sub><sup>dev</sup></sub> | <sub><sup>@"
            rval += beneficiary["account"]
            rval += "</sup></sub> | <sub><sup>"
            rval += str(share)
            rval += "%</sup></sub> | <sub><sup>author of "
            rval += prod
            rval += "</sup></sub> |\n"
    return rval

class ArchaeologyBot(BaseBot):
    """Example of an aiohivebot python bot without real utility"""

    def __init__(self, owner):
        self._owner = owner
        super().__init__()

    async def _comment_is_candidate(self, comment, author):
        try:
            last_payout =  parse(comment.get("last_payout", "2020-12-31T23:59:59")).timestamp()
        except TypeError:
            return None
        if last_payout < 24 * 3600:
            beneficiaries = comment.get("beneficiaries", [])
            allow_curation_rewards = comment.get("allow_curation_rewards", False)
            total_ben_cnt = 0
            total_ben_val = 0
            # Check if the comment has the post author set as (>=50%) beneficiary)
            for beneficiary in beneficiaries:
                if beneficiary.get("account", "") == author:
                    if (not (beneficiary.get("weight", 0) > 7999 or
                            (allow_curation_rewards and beneficiary.get("weight", 0) > 5999))):
                        return None
                else:
                    return None
            valid_beneficiaries = {author}
            total_ben_cnt = 0
            total_ben_val = 0
            for _, value in AUTHORS.items():
                valid_beneficiaries.add(value[1])
            all_ok = True
            for beneficiary in beneficiaries:
                if beneficiary.get("account", "") not in valid_beneficiaries:
                    all_ok = False
                total_ben_val += beneficiary.get("weight", 0)
                total_ben_cnt += 1
            if all_ok:
                if total_ben_cnt == 1 or total_ben_cnt == len(valid_beneficiaries):
                    if total_ben_val == 10000:
                        return [comment.get("author", None), comment.get("permlink", None)]
        return None


    async def _make_comment(self, author, permlink):
        code_author_share = int(5 * 100 / len(AUTHORS))
        post_author_share = 10000 - len(AUTHORS) * code_author_share
        benef = [{"account": author, "weight": post_author_share }]
        if code_author_share:
            for _, val in AUTHORS.items():
                if val[1] != author:
                    benef.append({"account": val[1], "weight": code_author_share})
                else:
                    benef[0]["weight"] += code_author_share
        benef = sorted(benef, key=operator.itemgetter('account'))
        body = make_body(author, benef, False)
        com_permlink = author.replace(".","-") + "-" + permlink
        transaction = self.start_transaction()
        transaction.comment(parent_author=author,
                            parent_permlink=permlink,
                            author=self._owner,
                            permlink=com_permlink,
                            title="Hive Archeology comment",
                            body=body,
                            json_metadata= json.dumps({
                                "tags": ["hivearcheology"],
                                "app": "HiveArcheology " + VERSION
                            }))
        transaction.comment_options(author=self._owner,
                                    permlink=com_permlink,
                                    max_accepted_payout="1000.000 HBD",
                                    percent_hbd=0,
                                    allow_votes=True,
                                    allow_curation_rewards=False,
                                    extensions=[[ 0, { "beneficiaries": benef }]])
        await transaction()

    async def _make_comment_if_unique(self, author, permlink):
        comments = await self.condenser_api.get_content_replies(author, permlink)
        candidate = None
        for comment in comments:
            if candidate is None:
                candidate = await self._comment_is_candidate(comment, author)
        if candidate is None or candidate[0] is None or candidate[1] is None:
            await self._make_comment(author, permlink)

    async def vote_operation(self, body):
        """Handler for cote_operation type operations in the HIVE block stream"""
        if "voter" in body and "author" in body and "permlink" in body:
            author = body["author"]
            permlink = body["permlink"]
            # weight = body["weight"]
            voter = body["voter"]
            if voter == self._owner:
                content = await self.bridge.get_post(author=author, permlink=permlink)
                if content and "is_paidout" in content and content["is_paidout"]:
                    await self._make_comment_if_unique(author, permlink)

pncset = ArchaeologyBot("pibara")
pncset.set_key("boguskey")
loop = asyncio.get_event_loop()
loop.run_until_complete(pncset.run())
print("Done")

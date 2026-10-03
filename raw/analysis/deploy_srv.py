import asyncio, base64, json, secrets, sys
sys.path.insert(0, "/work/skills/integrations/railway/scripts")
from railway_gql import gql, gql_mutation_draft
P="3f7dd783-451a-4a49-88e1-c20b87ed627c"; ENV="21383b2c-5b22-49e7-bb7e-036114835e9a"; S="96caf635-a9c7-4385-838c-b40f18302984"
async def main():
    tok = secrets.token_hex(16)
    open("/work/temp/r24/token.txt","w").write(tok)
    b64 = base64.b64encode(open("/work/temp/r24/srv.py","rb").read()).decode()
    r = await gql_mutation_draft("mutation($i:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$i)}",
        {"i":{"projectId":P,"environmentId":ENV,"serviceId":S,"variables":{"SRV_B64":b64,"DL_TOKEN":tok,"PORT":"8080"},"skipDeploys":True}})
    print("upsert", r)
    cmd = "/bin/sh -c 'ls -la /var/minis/shared/ledgers > /var/minis/shared/ledgers/_ls.txt; printf %s \"$SRV_B64\" | base64 -d > /tmp/srv.py && exec python /tmp/srv.py'"
    r = await gql_mutation_draft("mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}",
        {"s":S,"e":ENV,"i":{"startCommand":cmd}})
    print("update", r)
    r = await gql_mutation_draft("mutation($i:ServiceDomainCreateInput!){serviceDomainCreate(input:$i){id domain}}",
        {"i":{"serviceId":S,"environmentId":ENV,"targetPort":8080}})
    print("domain", r)
    r = await gql_mutation_draft("mutation($s:String!,$e:String!){serviceInstanceDeployV2(serviceId:$s,environmentId:$e)}", {"s":S,"e":ENV})
    print("deploy", r)
asyncio.run(main())

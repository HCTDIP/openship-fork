import asyncio, sys
sys.path.insert(0, "/work/skills/integrations/railway/scripts")
from railway_gql import gql_mutation_draft
async def main():
    r=await gql_mutation_draft('mutation{serviceDomainDelete(id:"5ff8e5a6-013b-4f7a-939a-1c884b347f04")}'); print(r["content"][:200])
    r=await gql_mutation_draft('mutation{deploymentStop(id:"88c650d5-1e3a-409e-bd51-4b8e9718df2d")}'); print(r["content"][:200])
asyncio.run(main())

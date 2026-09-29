import rqdatac

"""检查 RQData License 是否有效"""
try:
    rqdatac.init()
    print("✓ RQData License 有效")
except rqdatac.RQDataError:
    print("❌ RQData License 未激活或已过期")
    print("申请试用: https://www.ricequant.com/welcome/trial/rqsdk-cloud")
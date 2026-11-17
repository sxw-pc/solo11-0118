# 公交遗失物交接台

车辆收班后离线登记拾获物、封袋交接和认领证据，决定保管责任与是否交还。三个独立入口为 `found-import`、`custody-import`、`claim-import`；各自保存原始证据、业务实体、状态和跨链决定。所有数据为离线合成 fixture，不访问网络。

安装：`python -m pip install . --no-deps --no-build-isolation`。演示：`python -m lostproperty demo`。测试：`python -m pytest -q`。各入口接受 `--db` 和 `--file`；`report --db` 输出逐实体状态与来源。

拾获物绑定车次、座位、发现时间和离线乘车凭证。封袋交接按序号、保管人和封签核对；断签会阻断认领。两个不同见证人核实完整袋内实物后，`bag-reconcile` 可重封并恢复此前被保管链阻断的认领。竞合失主由 `claim-adjudicate` 引用乘车凭证、座位及物品特征选择归还者，落败申请保留；`return_event` 记录实际交还。命令 `demo-decisions` 展示断签、扣留和复核后的交还状态变化。

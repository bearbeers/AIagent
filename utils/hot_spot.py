from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import jieba
from typing import List, Dict, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class MunicipalHotspotRanker:
    """
    市政基础设施问题热度分析器
    功能：识别相似问题、聚类归集、生成热度排行榜
    """
    
    def __init__(self, similarity_threshold: float = 0.6, db_session: Optional['Session'] = None):
        """
        初始化热度分析器
        :param similarity_threshold: 相似度阈值（0-1之间），值越大要求越相似
        :param db_session: 数据库会话，如果提供则从数据库加载历史数据
        """
        self.reports = []  # 存储所有上报的问题
        self.report_cluster_map = {}  # 报告索引到聚类ID的映射
        self.clusters = {}  # 聚类信息 {cluster_id: {'representative': 代表文本, 'count': 数量, 'reports': [索引列表]}}
        self.cluster_counter = 0  # 聚类ID计数器
        
        # 中文分词器配置
        self.tokenizer = lambda text: ' '.join(jieba.cut(text))
        
        # TF-IDF向量化器，支持中文
        self.vectorizer = TfidfVectorizer(
            tokenizer=self.tokenizer,
            token_pattern=None,
            lowercase=False,
            max_features=5000,
            ngram_range=(1, 2)  # 支持1-gram和2-gram
        )
        self.tfidf_matrix = None
        self.similarity_threshold = similarity_threshold
        
        # 如果提供了数据库会话，从数据库加载历史数据
        if db_session is not None:
            self.load_from_database(db_session)

    def add_report(self, text: str) -> int:
        """
        添加一条新的市政问题上报记录，并自动进行相似度匹配和聚类
        :param text: 用户上报的问题文本
        :return: 新报告的索引
        """
        if not text or not text.strip():
            raise ValueError("问题文本不能为空")
        
        text = text.strip()
        report_idx = len(self.reports)
        self.reports.append(text)
        
        # 如果这是第一条报告，直接创建新聚类
        if report_idx == 0:
            self._create_new_cluster(report_idx, text)
            self.tfidf_matrix = self.vectorizer.fit_transform([text])
        else:
            # 先临时向量化新文本（使用已有vocabulary），用于相似度匹配
            # 如果vectorizer还未fit，则先fit所有已有报告
            if self.tfidf_matrix is None or self.tfidf_matrix.shape[0] == 0:
                self._rebuild_vectorizer()
            
            # 尝试匹配到现有聚类
            matched_cluster_id = self._find_matching_cluster(text)
            
            if matched_cluster_id is None:
                # 没有匹配到，创建新聚类
                self._create_new_cluster(report_idx, text)
            else:
                # 匹配到现有聚类，添加到该聚类
                self._add_to_cluster(report_idx, matched_cluster_id)
            
            # 重新构建向量矩阵（确保vocabulary包含所有新词）
            self._rebuild_vectorizer()
        
        return report_idx

    def _create_new_cluster(self, report_idx: int, text: str) -> int:
        """创建新的聚类"""
        cluster_id = self.cluster_counter
        self.cluster_counter += 1
        self.report_cluster_map[report_idx] = cluster_id
        self.clusters[cluster_id] = {
            'representative': text,
            'count': 1,
            'reports': [report_idx]
        }
        return cluster_id

    def _add_to_cluster(self, report_idx: int, cluster_id: int):
        """将报告添加到指定聚类"""
        self.report_cluster_map[report_idx] = cluster_id
        self.clusters[cluster_id]['count'] += 1
        self.clusters[cluster_id]['reports'].append(report_idx)

    def _find_matching_cluster(self, text: str) -> Optional[int]:
        """
        查找与新文本最匹配的聚类
        :param text: 待匹配的文本
        :return: 匹配的聚类ID，如果没有匹配则返回None
        """
        if not self.clusters or self.tfidf_matrix is None or self.tfidf_matrix.shape[0] == 0:
            return None
        
        try:
            # 尝试向量化新文本（使用现有vocabulary）
            new_vector = self.vectorizer.transform([text])
        except:
            # 如果transform失败（例如vocabulary不包含新词），返回None，让调用者重建vectorizer
            return None
        
        # 计算与每个聚类代表文本的相似度
        best_similarity = 0.0
        best_cluster_id = None
        
        # 只与现有聚类的代表文本比较（提高效率）
        for cluster_id, cluster_info in self.clusters.items():
            representative_idx = cluster_info['reports'][0]  # 使用第一个报告作为代表
            
            # 获取代表文本的向量
            if representative_idx < self.tfidf_matrix.shape[0]:
                rep_vector = self.tfidf_matrix[representative_idx:representative_idx+1]
                
                # 计算余弦相似度
                similarity = cosine_similarity(new_vector, rep_vector)[0][0]
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_cluster_id = cluster_id
        
        # 如果相似度超过阈值，返回匹配的聚类ID
        if best_similarity >= self.similarity_threshold:
            return best_cluster_id
        
        return None

    def _rebuild_vectorizer(self):
        """重新构建向量化器（当添加新文本导致vocabulary变化时）"""
        if len(self.reports) > 0:
            self.tfidf_matrix = self.vectorizer.fit_transform(self.reports)

    def find_similar_reports(self, text: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        找出与给定文本最相似的历史报告
        :param text: 待匹配的问题文本
        :param top_k: 返回最相似的前k个
        :return: [(相似报告, 相似度), ...]，按相似度降序排列
        """
        if not self.reports or self.tfidf_matrix is None:
            return []
        
        # 向量化查询文本
        query_vector = self.vectorizer.transform([text])
        
        # 计算与所有报告的相似度
        similarities = cosine_similarity(query_vector, self.tfidf_matrix)[0]
        
        # 获取top_k个最相似的结果
        top_indices = np.argsort(similarities)[::-1][:top_k]
        results = [(self.reports[i], similarities[i]) for i in top_indices if similarities[i] >= self.similarity_threshold]
        
        return results

    def get_clusters(self) -> Dict[str, Dict]:
        """
        获取所有聚类信息
        :return: {聚类ID: {representative: 代表文本, count: 数量, reports: [报告列表]}}
        """
        result = {}
        for cluster_id, cluster_info in self.clusters.items():
            result[str(cluster_id)] = {
                'representative': cluster_info['representative'],
                'count': cluster_info['count'],
                'reports': [self.reports[idx] for idx in cluster_info['reports']]
            }
        return result

    def get_hotspot_ranking(self, top_k: int = 10) -> List[Tuple[str, int, int]]:
        """
        获取热度排行榜（按数量降序）
        :param top_k: 返回前多少条
        :return: [(代表问题文本, 数量, 聚类ID), ...]
        """
        if not self.clusters:
            return []
        
        # 按数量排序
        ranked = sorted(
            self.clusters.items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )
        
        # 返回前top_k个
        result = [
            (cluster_info['representative'], cluster_info['count'], cluster_id)
            for cluster_id, cluster_info in ranked[:top_k]
        ]
        
        return result

    def print_hotspot(self, top_k: int = 10) -> None:
        """
        打印热度排行，格式类似微博热搜
        """
        ranking = self.get_hotspot_ranking(top_k)
        
        if not ranking:
            print("\n暂无问题数据")
            return
        
        print("\n🔥 市政设施问题热度排行榜 🔥")
        print("=" * 60)
        for idx, (issue, count, cluster_id) in enumerate(ranking, start=1):
            # 添加热度标签
            if idx == 1:
                tag = "🔥"
            elif idx <= 3:
                tag = "⭐"
            else:
                tag = "  "
            print(f"{tag} {idx}. {issue}")
            print(f"   热度: {count} | 聚类ID: {cluster_id}")
        
        print("=" * 60)

    def get_cluster_reports(self, cluster_id: int) -> List[str]:
        """
        获取指定聚类中的所有报告
        :param cluster_id: 聚类ID
        :return: 报告列表
        """
        if cluster_id not in self.clusters:
            return []
        
        return [self.reports[idx] for idx in self.clusters[cluster_id]['reports']]

    def get_statistics(self) -> Dict:
        """
        获取统计信息
        :return: 统计信息字典
        """
        return {
            'total_reports': len(self.reports),
            'total_clusters': len(self.clusters),
            'avg_reports_per_cluster': len(self.reports) / len(self.clusters) if self.clusters else 0
        }

    def load_from_database(self, db_session: 'Session'):
        """
        从数据库加载历史报告数据并重建聚类
        :param db_session: 数据库会话
        """
        try:
            # 延迟导入避免循环依赖
            from model.db import WorkOrderNumberTable
            
            # 查询待受理工单：未处理且未完成评分的工单
            # 已完成评分的工单（work_form_score不为None且不为0）不应该出现在待受理列表中
            user_reports = db_session.query(WorkOrderNumberTable).filter(
                WorkOrderNumberTable.work_content.isnot(None),
                WorkOrderNumberTable.work_content != '',
                WorkOrderNumberTable.work_status == '未处理',
                # 排除已完成评分的工单：work_form_score为None或0
                ((WorkOrderNumberTable.work_form_score.is_(None)) |
                 (WorkOrderNumberTable.work_form_score == 0.0))
            ).order_by(WorkOrderNumberTable.report_time.desc()).all()
            
            # 无论是否有数据，都先清空现有数据
            self.reports = []
            self.report_cluster_map = {}
            self.clusters = {}
            self.cluster_counter = 0
            self.tfidf_matrix = None
            
            if not user_reports:
                print("数据库中没有历史报告数据，已清空所有聚类")
                return
            
            # 加载所有报告内容
            print(f"正在从数据库加载 {len(user_reports)} 条历史报告...")
            for report in user_reports:
                if report.work_content and report.work_content.strip():
                    # 使用add_report方法添加，会自动进行聚类
                    self.add_report(report.work_content.strip())
            
            print(f"成功加载 {len(self.reports)} 条报告，形成 {len(self.clusters)} 个聚类")
            
        except Exception as e:
            print(f"从数据库加载数据失败: {e}")
            import traceback
            traceback.print_exc()
    
    def reload_from_database(self, db_session: 'Session'):
        """
        重新从数据库加载数据（用于刷新）
        :param db_session: 数据库会话
        """
        self.load_from_database(db_session)


# 示例使用
if __name__ == "__main__":
    # 创建热度分析器，设置相似度阈值为0.6
    ranker = MunicipalHotspotRanker(similarity_threshold=0.6)

    # 模拟用户随时上报的市政基础设施问题
    reports = [
        "水管爆裂严重，需要马上处理",
        "水管爆裂,导致大面积停水,请尽快处理",
        "自来水管道漏水，影响居民用水",
        "燃气泄漏风险，需要马上去处理",
        "燃气管道有异味，疑似泄漏",
        "下水道堵塞，污水外溢",
        "污水井盖破损，存在安全隐患",
        "供水主管道爆裂，导致大面积停水",
        "燃气报警器响起，怀疑泄漏",
        "路灯不亮，影响夜间出行安全",
        "道路照明灯故障，需要维修",
        "交通信号灯不工作，影响交通",
        "垃圾桶满了，需要清理",
        "垃圾箱溢出，异味严重",
    ]

    print("正在处理上报的问题...")
    for i, r in enumerate(reports, 1):
        ranker.add_report(r)
        print(f"已处理第 {i} 条上报")

    # 打印热度排行
    ranker.print_hotspot()

    # 打印统计信息
    print("\n📊 统计信息")
    stats = ranker.get_statistics()
    print(f"总上报数: {stats['total_reports']}")
    print(f"聚类数量: {stats['total_clusters']}")
    print(f"平均每类问题数: {stats['avg_reports_per_cluster']:.2f}")

    # 查看相似问题示例
    print("\n🔍 查找相似问题示例:")
    query = "水管爆裂"
    similar = ranker.find_similar_reports(query, top_k=3)
    print(f"查询: '{query}'")
    for report, similarity in similar:
        print(f"  - {report} (相似度: {similarity:.2f})")

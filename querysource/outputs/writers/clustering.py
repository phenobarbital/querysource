##################################################################
# Routine to identify the optimal number of cluster in a dataset #
##################################################################

import time
from io import StringIO, BytesIO
from typing import Any, Union
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.cluster import AgglomerativeClustering
from scipy.signal import argrelextrema
from aiohttp import web
from navconfig.logging import logging
from .abstract import AbstractWriter

matlog = logging.getLogger('matplotlib')
matlog.setLevel(logging.INFO)

pil = logging.getLogger('PIL.PngImagePlugin')
pil.setLevel(logging.INFO)

class ClusterWriter(AbstractWriter):
    mimetype: str = 'text/csv'
    extension: str = '.csv'
    ctype: str = 'csv'
    output_format: str = 'pandas'
    as_csv: bool = False

    def __init__(
        self,
        request: web.Request,
        resultset: Any,
        filename: str = None,
        response_type: str = 'web',
        download: bool = False,
        compression: Union[list, str] = None,
        ctype: str = None,
        **kwargs
    ):
        super(ClusterWriter, self).__init__(
            request,
            resultset,
            filename=filename,
            response_type=response_type,
            download=download,
            compression=compression,
            ctype=ctype,
            **kwargs
        )
        ### check if can change pdf library:
        if 'as_csv' in kwargs:
            self.as_cvs: bool = kwargs['as_csv']
            del kwargs['as_csv']
        else:
            self.as_csv: bool = False
        if self.as_csv is True:
            self.response_type = 'text/csv'
            self.extension = '.csv'

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        buffer = None
        selected_data_oh = pd.get_dummies(self.data)
        # calculate the silhouette score per each cluster identified in KMeans
        range_n_clusters = list (range(2,10))
        score_each_cluster = []
        for n_clusters in range_n_clusters:
            clusterer = KMeans (init='random', n_clusters=n_clusters).fit(selected_data_oh)
            preds = clusterer.fit_predict(selected_data_oh)
            score_cluster = silhouette_score(selected_data_oh, preds)
            score_each_cluster.append(score_cluster)
        # determine the local maximum silhouette score in the cluster set
        cluster_max = []
        score_max = []
        a = np.array(score_each_cluster)
        ind_max_score = argrelextrema(a, np.greater)
        for i in range(len(ind_max_score[0])):
            cluster = range_n_clusters[ind_max_score[0][i]]
            if i == 0:
                cluster_max = cluster
                score_max = score_each_cluster[cluster-2]
            elif score_max < score_each_cluster[cluster-2]:
                cluster_max = cluster
                score_max = score_each_cluster[cluster-2]
        ##########################################
        # Routine to label dataset with clusters #
        ##########################################
        # AgglomerativeClustering to label data with clusters
        num_clusters = cluster_max
        clustering_model = AgglomerativeClustering(
            n_clusters = num_clusters,
            affinity = 'euclidean',
            linkage = 'ward'
        )
        y_hc = clustering_model.fit(selected_data_oh)
        data_labels = y_hc.labels_
        # save dataset with labeled clusters
        label_cluster = list(data_labels)
        self.data['Cluster'] = label_cluster
        columns = list(self.data.columns)
        if self.as_csv is False:
            # create the HTML file:
            output = StringIO()
            self.data.to_html(
                output,
                columns=columns,
                header=True,
                index=False,
                classes='table table-stripped',
                bold_rows=True,
                # escape=True,
                border=1,
                show_dimensions=True,
                table_id="qs_table"
            )
            output.seek(0)
            buffer = bytes(output.getvalue(), 'utf-8')
        else:
            output = BytesIO()
            self.data.to_csv(
                output,
                columns=columns,
                header=True,
                index=False,
                encoding='UTF-8'
            )
        response = await self.response(self.response_type)
        content_length = len(buffer)
        response.content_length = content_length
        if self.download is True: # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, buffer)

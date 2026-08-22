import streamlit as st
import pandas as pd

st.title("첫 번째 앱")
st.write("잘 작동합니다!")

df = pd.DataFrame({"이름": ["가", "나", "다"], "값": [10, 20, 30]})
st.dataframe(df)
st.bar_chart(df.set_index("이름"))
